"""Utility helpers for mantaray."""


def tkinter_safe_string(string: str, *, hide_unsupported_chars: bool = False) -> str:
    """Return a tkinter-safe string by replacing unsupported Unicode characters.

    tkinter on some platforms cannot display Unicode codepoints above U+FFFF.
    Replace those characters with U+FFFD by default, or remove them entirely when
    hide_unsupported_chars is True.
    """
    if hide_unsupported_chars:
        replace_with = ""
    else:
        replace_with = "\N{REPLACEMENT CHARACTER}"

    return "".join(replace_with if ord(char) > 0xFFFF else char for char in string)
