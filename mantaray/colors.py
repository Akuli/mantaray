from __future__ import annotations
import re
import tkinter
from typing import Iterator

# https://www.mirc.com/colors.html
_MIRC_COLORS = {
    0: "#ffffff",
    1: "#000000",
    2: "#00007f",
    3: "#009300",
    4: "#ff0000",
    5: "#7f0000",
    6: "#9c009c",
    7: "#fc7f00",
    8: "#ffff00",
    9: "#00fc00",
    10: "#009393",
    11: "#00ffff",
    12: "#0000fc",
    13: "#ff00ff",
    14: "#7f7f7f",
    15: "#d2d2d2",
}

FOREGROUND = _MIRC_COLORS[0]
BACKGROUND = _MIRC_COLORS[1]


def parse_text(text: str) -> Iterator[tuple[str, list[str]]]:
    style_regex = r"\x02|\x1f|\x03[0-9]{1,2}(?:,[0-9]{1,2})?|\x0f"

    # parts contains matched parts of the regex followed by texts
    # between those matched parts
    parts = [""] + re.split("(" + style_regex + ")", text)
    assert len(parts) % 2 == 0

    fg = None
    bg = None
    underline = False

    for style_spec, substring in zip(parts[0::2], parts[1::2]):
        if not style_spec:
            # beginning of text
            pass
        elif style_spec == "\x02":
            # Bold not supported, because requires setting custom font in a tag
            # And then the tag's font would need to stay in sync with the main font
            pass
        elif style_spec == "\x1f":
            underline = True
        elif style_spec.startswith("\x03"):
            # color
            match = re.fullmatch(r"\x03([0-9]{1,2})(,[0-9]{1,2})?", style_spec)
            assert match is not None
            fg_spec, bg_spec = match.groups()

            # https://www.mirc.com/colors.html talks about big color numbers:
            # "The way these colors are interpreted varies from client to
            # client. Some map the numbers back to 0 to 15, others interpret
            # numbers larger than 15 as the default text color."
            #
            # i'm not sure how exactly the colors should be mapped to the
            # supported range, so i'll just use the default color thing
            fg = int(fg_spec)
            if fg not in _MIRC_COLORS:
                fg = None

            if bg_spec is not None:
                bg = int(bg_spec.lstrip(","))
                if bg not in _MIRC_COLORS:
                    bg = None
        elif style_spec == "\x0f":
            fg = None
            bg = None
            underline = False
        else:
            raise ValueError("unexpected regex match: " + repr(style_spec))

        if substring:
            tags = []
            if fg is not None:
                tags.append("foreground-%d" % fg)
            if bg is not None:
                tags.append("background-%d" % bg)
            if underline:
                tags.append("underline")
            yield (substring, tags)


def config_tags(textwidget: tkinter.Text) -> None:
    textwidget.config(fg=FOREGROUND, bg=BACKGROUND)

    textwidget.tag_configure("underline", underline=True)
    textwidget.tag_configure("pinged", foreground=_MIRC_COLORS[9])
    textwidget.tag_configure("error", foreground=_MIRC_COLORS[4])
    textwidget.tag_configure("info", foreground=_MIRC_COLORS[11])
    textwidget.tag_configure("history-selection", background=_MIRC_COLORS[12])
    # TODO: make nicks clickable
    textwidget.tag_configure("self-nick", foreground=_MIRC_COLORS[11], underline=True)
    textwidget.tag_configure("other-nick", foreground=_MIRC_COLORS[10], underline=True)

    for number, hexcolor in _MIRC_COLORS.items():
        textwidget.tag_configure(f"foreground-{number}", foreground=hexcolor)
        textwidget.tag_configure(f"background-{number}", background=hexcolor)
        textwidget.tag_raise(f"foreground-{number}", "pinged")
        textwidget.tag_raise(f"background-{number}", "pinged")
