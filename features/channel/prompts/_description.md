{{ summary }}

Sumber:
{% for url in sources %}
- {{ url }}
{% endfor %}
{% if uses_stock or uses_ai %}

{% if uses_stock %}
Video: Pexels/Pixabay
{% endif %}
{% if uses_ai %}
Sebagian visual dibuat dengan AI.
{% endif %}
{% endif %}

{{ hashtags | join(" ") }}
