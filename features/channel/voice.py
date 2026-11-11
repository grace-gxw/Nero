"""
Nero · Channel Otomatis - TTS dan timing kata (SPEC.md 6.7).

Setiap bagian narasi (hook, segmen, CTA) disuarakan terpisah dengan edge-tts, lalu disambung dengan jeda alami
(edge-tts 7.x tidak menerima SSML/<break>). Cara ini juga memberi batas segmen yang tepat untuk visual.
Timing per kata diambil dari WordBoundary edge-tts; bila tidak tersedia, dari faster-whisper (engine.transcribe).

Catatan edge-tts 7.3.1 (dicek di source & uji nyata, Nov 2026):
- boundary bawaan "SentenceBoundary"; harus diminta "WordBoundary". offset/duration dalam satuan 100 ns.
- Teks WordBoundary tanpa tanda baca; angka/satuan bisa satu kesatuan ("September 2007", "-272 °C").
- stream_sync() bisa menggantung selamanya bila terjadi galat, jadi dipakai stream() async sendiri.
- Di jaringan dengan IPv6 bermasalah, koneksi kadang langsung putus (WinError 64); IPv4 dicoba lebih dulu.

Hasil di folder run: voice.wav (dipakai render), voice.mp3, words.json.
"""
from __future__ import annotations

import asyncio
import os
import re
import shutil
import socket
import threading

import numpy as np

from features.clip import engine

from . import config, media
from .config import ChannelConfig
from .schemas import Script, SegmentTiming, Words, WordTiming

TICKS = 10_000_000            # satuan offset/duration edge-tts: 100 nanodetik
EDGE_ATTEMPTS = 3
EDGE_TIMEOUT = 60             # detik per bagian narasi
LEAD = 0.04                   # sisa hening sebelum kata pertama tiap bagian (detik)
TAIL = 0.14                   # sisa setelah kata terakhir (ujung konsonan tidak terpotong)
TAIL_SECONDS = config.VIDEO_TAIL_SECONDS   # video berlanjut sebentar setelah suara selesai
MAX_RATE = 15                 # SPEC 6.7: rate dinaikkan paling banyak sampai +15%
RATE_STEP = 5
RETRY_DELAY = 1.5


class TTSError(RuntimeError):
    """TTS gagal (pesan aman ditampilkan)."""


class TooLong(TTSError):
    def __init__(self, seconds: float, limit: float, rate: str):
        super().__init__(f"Narasi {seconds:.1f} dtk melebihi batas {limit:.0f} dtk walau rate sudah {rate}.")
        self.seconds, self.limit, self.rate = seconds, limit, rate


Mark = tuple[str, float, float]      # (kata, mulai, durasi) dalam detik


# --------------------------------------------------------------------------
# edge-tts
# --------------------------------------------------------------------------
async def _edge_stream(text: str, voice: str, rate: str, ipv4: bool,
                       cancel: threading.Event | None) -> tuple[bytes, list[Mark]]:
    import aiohttp
    import edge_tts
    conn = aiohttp.TCPConnector(family=socket.AF_INET) if ipv4 else None
    comm = edge_tts.Communicate(text, voice, rate=rate, boundary="WordBoundary", connector=conn,
                                connect_timeout=10, receive_timeout=EDGE_TIMEOUT)
    audio = bytearray()
    marks: list[Mark] = []
    async for chunk in comm.stream():
        media.check_cancel(cancel)
        kind = chunk.get("type")
        if kind == "audio":
            audio += chunk.get("data") or b""
        elif kind == "WordBoundary":
            word = str(chunk.get("text") or "").strip()
            if word:
                marks.append((word, chunk["offset"] / TICKS, chunk["duration"] / TICKS))
    if not audio:
        raise TTSError("edge-tts tidak mengembalikan audio.")
    return bytes(audio), marks


async def _edge_all(texts: list[str], voice: str, rate: str, log, cancel, progress) -> list[tuple[bytes, list[Mark]]]:
    out = []
    for k, text in enumerate(texts):
        last: Exception | None = None
        for attempt in range(EDGE_ATTEMPTS):
            media.check_cancel(cancel)
            try:
                got = await asyncio.wait_for(_edge_stream(text, voice, rate, attempt % 2 == 0, cancel), EDGE_TIMEOUT)
                break
            except media.Cancelled:
                raise
            except Exception as e:  # noqa: BLE001  galat jaringan/layanan edge-tts beragam jenisnya
                last = e
                log(f"tts: edge-tts gagal ({type(e).__name__}: {str(e)[:140]}), "
                    f"percobaan {attempt + 1}/{EDGE_ATTEMPTS}")
                if attempt + 1 < EDGE_ATTEMPTS:
                    await asyncio.sleep(RETRY_DELAY * (attempt + 1))
        else:
            raise TTSError(f"edge-tts gagal {EDGE_ATTEMPTS} kali: {type(last).__name__}: {last}")
        out.append(got)
        if progress:
            progress((k + 1) / len(texts))
    return out


def edge_synthesize(texts: list[str], voice: str, rate: str, *, log=print, cancel=None,
                    progress=None) -> list[tuple[bytes, list[Mark]]]:
    """Suarakan tiap teks: [(audio mp3, [(kata, mulai, durasi)])]. Satu event loop untuk semua bagian."""
    return asyncio.run(_edge_all(texts, voice, rate, log, cancel, progress))


# --------------------------------------------------------------------------
# Penyusunan
# --------------------------------------------------------------------------
_NON_WORD = re.compile(r"[\W_]+", re.UNICODE)


def _norm(w: str) -> str:
    return _NON_WORD.sub("", w.lower())


def attach_punctuation(text: str, marks: list[Mark]) -> list[Mark]:
    """Ganti kata WordBoundary dengan token asli naskah (dengan tanda baca, mis. 'angkasa?'). Satu WordBoundary
    bisa mencakup beberapa token ('September 2007', '-272 °C')."""
    tokens = text.split()
    out, i = [], 0
    for word, start, dur in marks:
        nw = _norm(word)
        picked = word
        if nw:
            found = False
            for j in range(i, min(len(tokens), i + 4)):
                for k in range(1, 4):
                    if j + k > len(tokens):
                        break
                    if _norm("".join(tokens[j:j + k])) == nw:
                        picked, i, found = " ".join(tokens[j:j + k]), j + k, True
                        break
                if found:
                    break
        out.append((picked, start, dur))
    return out


def speech_bounds(samples: np.ndarray, rate: int = media.SAMPLE_RATE, threshold: float = 0.02) -> tuple[float, float]:
    """Awal & akhir suara (detik) menurut energi, untuk memotong hening di ujung potongan."""
    if samples.size == 0:
        return 0.0, 0.0
    hop = rate // 100
    n = samples.size // hop
    if n == 0:
        return 0.0, samples.size / rate
    frames = np.abs(samples[:n * hop].astype(np.float32).reshape(n, hop)).max(axis=1) / 32768.0
    loud = np.nonzero(frames >= threshold)[0]
    if loud.size == 0:
        return 0.0, samples.size / rate
    return loud[0] * hop / rate, min(samples.size, (loud[-1] + 1) * hop) / rate


def pause_after(prev_id: int, next_id: int, pause: float) -> float:
    """Jeda antarbagian: sedikit lebih panjang setelah hook dan sebelum CTA."""
    if prev_id == 0 or next_id == -1:
        return pause * 1.3
    return pause


def assemble(pieces: list[tuple[int, str, np.ndarray, list[Mark]]], pause: float,
             rate: int = media.SAMPLE_RATE) -> tuple[np.ndarray, list[SegmentTiming], list[WordTiming]]:
    """Sambung potongan suara [(segment_id, teks, sampel int16, marks)] -> (sampel, timing segmen, timing kata).
    Hening di awal/akhir tiap potongan dipangkas, lalu disisipkan jeda yang seragam."""
    out: list[np.ndarray] = []
    timings: list[SegmentTiming] = []
    words: list[WordTiming] = []
    cursor = 0.0
    prev = None
    for seg_id, text, samples, marks in pieces:
        if prev is not None:
            n = round(pause_after(prev, seg_id, pause) * rate)
            out.append(np.zeros(n, dtype=np.int16))
            cursor += n / rate
        e0, e1 = speech_bounds(samples, rate)
        s0, s1 = (min(e0, marks[0][1]), max(e1, marks[-1][1] + marks[-1][2])) if marks else (e0, e1)
        a = max(0, int((s0 - LEAD) * rate))
        b = min(samples.size, int(np.ceil((s1 + TAIL) * rate)))
        if b <= a:
            a, b = 0, samples.size
        clip = samples[a:b]
        start = cursor
        length = clip.size / rate
        for word, ws, wd in attach_punctuation(text, marks):
            t0 = min(length, max(0.0, ws - a / rate))
            t1 = min(length, t0 + max(0.05, wd))
            words.append(WordTiming(word=word, start=round(start + t0, 3), end=round(start + t1, 3),
                                    segment_id=seg_id))
        out.append(clip)
        cursor += length
        timings.append(SegmentTiming(segment_id=seg_id, start=round(start, 3),
                                     speech_start=round(start + max(0.0, s0 - a / rate), 3), end=round(cursor, 3)))
        prev = seg_id
    joined = np.concatenate(out) if out else np.zeros(0, dtype=np.int16)
    return joined, timings, words


def whisper_words(wav_path: str, timings: list[SegmentTiming], log) -> list[WordTiming]:
    """Cadangan timing: transkrip voice.wav dengan faster-whisper; kata dibagi ke segmen menurut waktunya."""
    log("tts: timing kata dari faster-whisper (WordBoundary tidak lengkap)")
    wav16 = wav_path + ".16k.wav"
    try:
        engine.extract_audio(wav_path, wav16)       # transcribe() mengharuskan 16 kHz mono
        segs = engine.transcribe(wav16, model_size="base", language="id")
    finally:
        if os.path.exists(wav16):
            os.remove(wav16)
    out = []
    for s in segs:
        for w in s.get("words") or []:
            text = str(w.get("word", "")).strip()
            if not text:
                continue
            mid = (w["start"] + w["end"]) / 2
            # bagian terakhir yang sudah mulai (kata di jeda ikut bagian sebelumnya)
            seg = next((t.segment_id for t in reversed(timings) if t.start <= mid), timings[0].segment_id)
            out.append(WordTiming(word=text, start=round(w["start"], 3), end=round(w["end"], 3), segment_id=seg))
    return out


def _rate_value(rate: str) -> int:
    return int(rate.strip().rstrip("%"))


def _rate_text(v: int) -> str:
    return f"{v:+d}%"


def synthesize(script: Script, cfg: ChannelConfig, out_dir: str, *, log=print,
               cancel: threading.Event | None = None, progress=None) -> Words:
    """Buat voice.wav, voice.mp3, dan words.json di out_dir.

    Durasi (suara + ekor video) di atas batas -> rate dinaikkan bertahap sampai +15%. Bila masih kelebihan ->
    TooLong, dan pipeline meminta naskah dipendekkan. Di bawah batas minimal -> hanya peringatan (SPEC 6.7)."""
    if cfg.tts.engine != "edge":
        raise TTSError("Mesin TTS 'piper' belum tersedia (suara Indonesia Piper belum jelas lisensinya). "
                       "Pakai 'edge'.")
    voice = cfg.tts.voice
    lo, hi = cfg.video.target_seconds
    rate_v = _rate_value(cfg.tts.rate)
    work = os.path.join(out_dir, "tts")
    os.makedirs(work, exist_ok=True)
    narration = [(sid, t.strip()) for sid, t in script.narration() if t.strip()]
    pause = cfg.tts.pause_ms / 1000
    try:
        return _synthesize(narration, cfg, out_dir, work, voice, lo, hi, rate_v, pause, log, cancel, progress)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _synthesize(narration, cfg: ChannelConfig, out_dir: str, work: str, voice: str, lo: float, hi: float,
                rate_v: int, pause: float, log, cancel, progress) -> Words:
    while True:
        rate = _rate_text(rate_v)
        log(f"tts: {voice}, rate {rate}, {len(narration)} bagian")
        voiced = edge_synthesize([t for _, t in narration], voice, rate, log=log, cancel=cancel,
                                 progress=(lambda p: progress(0.9 * p)) if progress else None)
        pieces = []
        for k, ((sid, text), (audio, marks)) in enumerate(zip(narration, voiced, strict=True)):
            path = os.path.join(work, f"part_{k:02d}.mp3")
            with open(path, "wb") as f:
                f.write(audio)
            pieces.append((sid, text, media.decode_to_pcm(path, cancel=cancel), marks))
        samples, timings, words = assemble(pieces, pause)
        total = samples.size / media.SAMPLE_RATE + TAIL_SECONDS
        if total <= hi or rate_v >= MAX_RATE:
            break
        new = min(MAX_RATE, rate_v + RATE_STEP)
        log(f"tts: durasi {total:.1f} dtk > {hi} dtk, rate dinaikkan ke {_rate_text(new)}")
        rate_v = new
    if total > hi:
        raise TooLong(total, hi, rate)
    wav = os.path.join(out_dir, "voice.wav")
    media.write_wav(wav, samples)
    source = "edge"
    if not all(p[3] for p in pieces):     # ada bagian tanpa WordBoundary: timing seluruhnya dari Whisper
        words = whisper_words(wav, timings, log)
        source = "whisper"
    warnings: list[str] = []
    if total < lo:
        warnings.append(f"Durasi {total:.1f} dtk di bawah target minimal {lo} dtk.")
        log(f"tts: PERINGATAN durasi {total:.1f} dtk di bawah target minimal {lo} dtk")
    media.ffmpeg(["-i", wav, "-c:a", "libmp3lame", "-b:a", "128k", os.path.join(out_dir, "voice.mp3")],
                 cancel=cancel)
    if progress:
        progress(1.0)
    log(f"tts: selesai, suara {samples.size / media.SAMPLE_RATE:.1f} dtk, {len(words)} kata (timing: {source})")
    return Words(words=words, duration=round(samples.size / media.SAMPLE_RATE, 3), source=source,
                 segments=timings, voice=voice, rate=rate, warnings=warnings)
