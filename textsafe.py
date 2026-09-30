"""HTML-escaping helper for Telegram parse_mode="HTML" messages.

User-controlled values (name, bio, city, interests, ...) must be escaped
before being interpolated into HTML text, otherwise a crafted profile can
inject clickable links / markup into other users' Telegram messages or
break the send entirely (Telegram rejects malformed HTML).
"""

import html


def esc(value) -> str:
    """Escape a value for Telegram HTML. None becomes an empty string."""
    if value is None:
        return ""
    # quote=False: Telegram HTML supports only &amp; &lt; &gt; entities.
    return html.escape(str(value), quote=False)
