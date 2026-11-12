"""
Nero · Channel Otomatis - pembantu FFmpeg untuk langkah media (TTS, visual, render).

Semua klip antara dibuat dengan format yang sama persis (1080×1920, 30 fps, H.264 yuv420p, tanpa audio), supaya
bisa disambung tanpa encode ulang. Jumlah frame dihitung dari waktu dengan pembulatan yang sama di semua langkah,
jadi durasi video tidak bergeser sedikit demi sedikit.
"""
from __future__ import annotations

import os
import subprocess
import threading
import wave
from collections.abc import Callable, Iterable

import numpy as np

from features.clip import engine

W, H = engine.OUT_W, engine.OUT_H          # 1080 × 1920
FPS = engine.OUT_FPS                        # 30
SAMPLE_RATE = 24000                         # suara edge-tts: 24 kHz mono

# Semua klip antara memakai warna BT.709 rentang TV dan diberi tanda yang sama. Tanpa ini, klip stok (BT.709 /
# rentang penuh) dan grafik/gambar AI (BT.601 tanpa tanda) yang disambung tanpa encode ulang dibaca dengan aturan
# warna klip pertama, sehingga warnanya bergeser/pudar.
TO_BT709 = "scale=out_color_matrix=bt709:out_range=tv,format=yuv420p"
COLOR_TAGS = ["-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv"]
# encode klip antara: cepat, kualitas tinggi (render akhir meng-encode ulang)
CLIP_CODEC = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
              "-profile:v", "high", "-g", str(FPS * 2), *COLOR_TAGS, "-an"]


# pembatalan ada di modul ringan cancel.py (dipakai juga langkah rencana); diekspor ulang di sini
from .cancel import Cancelled, check_cancel  # noqa: F401


def frame_at(t: float, fps: int = FPS) -> int:
    """Nomor frame untuk waktu t (detik). Dipakai di semua langkah supaya batas segmen selalu sama."""
    return round(max(0.0, t) * fps)


def ffmpeg(args: list[str], *, cancel: threading.Event | None = None, stdin_frames: Iterable[bytes] | None = None,
           timeout: float = 900) -> None:
    """Jalankan ffmpeg. Bisa dibatalkan (proses dihentikan) dan, bila diberi, mengirim frame mentah lewat stdin."""
    check_cancel(cancel)
    cmd = [engine.ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y",
           *(["-nostdin"] if stdin_frames is None else []), *args]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE if stdin_frames is not None else subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, **engine._popen_kwargs())
    err: list[bytes] = []
    reader = threading.Thread(target=lambda: err.append(proc.stderr.read()), daemon=True)
    reader.start()
    try:
        if stdin_frames is not None:
            try:
                for chunk in stdin_frames:          # galat pembuat frame (mis. OSError Pillow) diteruskan
                    check_cancel(cancel)
                    try:
                        proc.stdin.write(chunk)
                    except OSError:
                        break      # ffmpeg berhenti lebih dulu; pesan galatnya dibaca di bawah
            finally:
                try:
                    proc.stdin.close()
                except OSError:
                    pass
        waited = 0.0
        while True:
            try:
                proc.wait(timeout=0.25)
                break
            except subprocess.TimeoutExpired:
                waited += 0.25
                check_cancel(cancel)
                if waited > timeout:
                    raise RuntimeError(f"FFmpeg terlalu lama (lebih dari {timeout:.0f} detik).") from None
    except BaseException:
        proc.kill()
        proc.wait()
        reader.join(timeout=5)
        proc.stderr.close()
        raise
    reader.join(timeout=5)
    proc.stderr.close()
    if proc.returncode != 0:
        msg = (err[0] if err else b"").decode("utf-8", "ignore").strip()
        raise RuntimeError("FFmpeg gagal: " + msg[-800:])


def encode_frames(frames: Iterable[np.ndarray], n_frames: int, out_path: str, *,
                  cancel: threading.Event | None = None) -> None:
    """Encode frame RGB (H×W×3 uint8) menjadi klip antara berisi tepat n_frames frame."""
    sent = 0

    def chunks():
        nonlocal sent
        last = None
        for f in frames:
            if sent >= n_frames:
                break
            last = np.ascontiguousarray(f, dtype=np.uint8)
            yield last.tobytes()
            sent += 1
        while last is not None and sent < n_frames:   # pembuat frame berhenti lebih awal: ulang frame terakhir
            yield last.tobytes()
            sent += 1
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    ffmpeg(["-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
            "-vf", TO_BT709, *CLIP_CODEC, "-frames:v", str(n_frames), out_path], cancel=cancel,
           stdin_frames=chunks())
    if sent != n_frames:
        raise RuntimeError(f"klip hanya berisi {sent} dari {n_frames} frame")


def frames_of(make: Callable[[int], np.ndarray], n_frames: int) -> Iterable[np.ndarray]:
    for i in range(n_frames):
        yield make(i)


# --------------------------------------------------------------------------
# Audio PCM (wav 16-bit mono)
# --------------------------------------------------------------------------
def decode_to_pcm(src: str, *, rate: int = SAMPLE_RATE, cancel: threading.Event | None = None) -> np.ndarray:
    """Baca berkas audio apa pun menjadi sampel int16 mono pada `rate` Hz."""
    tmp = src + ".pcm.wav"
    try:
        ffmpeg(["-i", src, "-vn", "-ac", "1", "-ar", str(rate), "-c:a", "pcm_s16le", tmp], cancel=cancel)
        return read_wav(tmp)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def read_wav(path: str) -> np.ndarray:
    with wave.open(path, "rb") as w:
        if w.getsampwidth() != 2:
            raise RuntimeError("wav harus 16-bit")
        data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
        if w.getnchannels() > 1:
            data = data.reshape(-1, w.getnchannels()).mean(axis=1).astype(np.int16)
        return data


def write_wav(path: str, samples: np.ndarray, rate: int = SAMPLE_RATE) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with wave.open(tmp, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(np.asarray(samples, dtype=np.int16).tobytes())
    os.replace(tmp, path)


def audio_seconds(path: str) -> float:
    """Panjang audio sebuah berkas (detik); 0 bila tidak ada stream audio atau berkas tidak terbaca."""
    import re
    try:
        r = subprocess.run([engine.ffmpeg_exe(), "-hide_banner", "-i", path], capture_output=True, timeout=30,
                           check=False, **engine._popen_kwargs())
    except (OSError, subprocess.TimeoutExpired):
        return 0.0
    txt = r.stderr.decode("utf-8", "ignore")
    if not any("Audio:" in line for line in txt.splitlines()):
        return 0.0
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", txt)
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)) if m else 0.0


HDR_MARKERS = ("bt2020", "smpte2084", "arib-std-b67")


def video_seconds(path: str) -> float:
    """Panjang stream VIDEO sebuah berkas (detik), bukan durasi kontainer (audio bisa lebih panjang). 0 bila
    tidak terbaca. ValueError untuk video HDR (warnanya pudar bila dipakai apa adanya)."""
    import re
    try:
        r = subprocess.run([engine.ffmpeg_exe(), "-hide_banner", "-i", path, "-map", "0:v:0", "-c", "copy",
                            "-f", "null", "-"], capture_output=True, timeout=120, check=False,
                           **engine._popen_kwargs())
    except (OSError, subprocess.TimeoutExpired):
        return 0.0
    txt = r.stderr.decode("utf-8", "ignore")
    vline = next((line for line in txt.splitlines() if "Video:" in line), "")
    if any(m in vline for m in HDR_MARKERS):
        raise ValueError("klip HDR")
    times = re.findall(r"time=(\d+):(\d+):(\d+(?:\.\d+)?)", txt)
    if r.returncode != 0 or not vline or not times:
        return 0.0
    h, m, s = times[-1]
    return int(h) * 3600 + int(m) * 60 + float(s)


def wav_duration(path: str) -> float:
    with wave.open(path, "rb") as w:
        return w.getnframes() / float(w.getframerate())
