from __future__ import annotations

import re
import tkinter
from functools import partial
from typing import Callable, Iterator

from mantaray.right_click_menus import RIGHT_CLICK_BINDINGS

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
BACKGROUND = "#242323"


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


def find_and_tag_urls(textwidget: tkinter.Text, start: str, end: str) -> None:
    search_start = start
    while True:
        match_start = textwidget.search(
            r"\mhttps?://[a-z0-9:]", search_start, end, nocase=True, regexp=True
        )
        if not match_start:  # empty string means not found
            break

        url = textwidget.get(match_start, f"{match_start} lineend")

        url = url.split()[0]
        url = url.split("'")[0]
        url = url.split('"')[0]
        url = url.split("`")[0]

        # URL, and URL. URL? URL! (also URL). (also URL.)
        url = url.rstrip(".,?!")
        if "(" not in url:  # urls can contain spaces (e.g. wikipedia)
            url = url.rstrip(")")
        url = url.rstrip(".,?!")

        match_end = f"{match_start} + {len(url)} chars"
        textwidget.tag_add("url", match_start, match_end)
        textwidget.tag_remove("self-nick", match_start, match_end)
        textwidget.tag_remove("other-nick", match_start, match_end)
        search_start = f"{match_end} + 1 char"


def _on_link_clicked(
    tag: str,
    callback: Callable[[tkinter.Event[tkinter.Text], str, str], None],
    event: tkinter.Event[tkinter.Text],
) -> None:
    # To test this, set up 3 URLs, and try clicking first and last char of middle URL.
    # That finds bugs where it finds the wrong URL, or only works in the middle of URL, etc.
    tag_range = event.widget.tag_prevrange(tag, "current + 1 char")
    assert tag_range
    start, end = tag_range
    text = event.widget.get(start, end)
    callback(event, tag, text)


def config_tags(
    textwidget: tkinter.Text,
    left_click_callback: Callable[[tkinter.Event[tkinter.Text], str, str], None],
    right_click_callback: Callable[[tkinter.Event[tkinter.Text], str, str], None],
) -> None:
    textwidget.config(fg=FOREGROUND, bg=BACKGROUND)

    textwidget.tag_configure("url", underline=True)
    textwidget.tag_configure("underline", underline=True)
    textwidget.tag_configure("pinged", foreground="#a1e37b")
    textwidget.tag_configure("error", foreground="#bd2f2f")
    textwidget.tag_configure("info", foreground="#FFE6C7")
    textwidget.tag_configure("history-selection", background="#5a5c50")
    textwidget.tag_configure("channel", foreground="#f7e452")
    textwidget.tag_configure("topic", foreground="#a2e0de")
    textwidget.tag_configure("self-nick", foreground="#de8c28", underline=True)
    textwidget.tag_configure("other-nick", foreground="#e7b678", underline=True)
    textwidget.tag_configure("privmsg", foreground=FOREGROUND)

    for lower_tag in ["info", "error", "privmsg"]:
        textwidget.tag_lower(lower_tag, "pinged")
    for upper_tag in ["history-selection", "channel", "self-nick", "other-nick"]:
        textwidget.tag_raise(upper_tag, "pinged")

    for number, hexcolor in _MIRC_COLORS.items():
        textwidget.tag_configure(f"foreground-{number}", foreground=hexcolor)
        textwidget.tag_configure(f"background-{number}", background=hexcolor)
        textwidget.tag_raise(f"foreground-{number}", "privmsg")
        textwidget.tag_raise(f"background-{number}", "privmsg")

    default_cursor = textwidget["cursor"]
    for tag in ["url", "other-nick"]:
        textwidget.tag_bind(
            tag, "<Button-1>", partial(_on_link_clicked, tag, left_click_callback)
        )
        for right_click in RIGHT_CLICK_BINDINGS:
            textwidget.tag_bind(
                tag, right_click, partial(_on_link_clicked, tag, right_click_callback)
            )
        textwidget.tag_bind(
            tag, "<Enter>", (lambda e: textwidget.config(cursor="hand2"))
        )
        textwidget.tag_bind(
            tag, "<Leave>", (lambda e: textwidget.config(cursor=default_cursor))
        )
