from __future__ import annotations
import re
import tkinter
import tkinter.font as tkfont
from typing import Any, Iterator

from . import backend

# (he)xchat supports these
_BOLD = "\x02"
_UNDERLINE = "\x1f"
_COLOR = "\x03"  # followed by N or N,M where N and M are 1 or 2 digit numbers
_BACK_TO_NORMAL = "\x0f"


# https://www.mirc.com/colors.html
_MIRC_COLORS = {
    0: "#000000",
    1: "#ffffff",
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

# uncomment if you don't want a dark background
_MIRC_COLORS[0], _MIRC_COLORS[1] = _MIRC_COLORS[1], _MIRC_COLORS[0]

# avoid dark colors, black, white and grays
# 9 is green, would conflict with pinged tag
_NICK_COLORS = sorted(_MIRC_COLORS.keys() - {0, 1, 2, 9, 14, 15})

INFO_PREFIX = _COLOR + "11"
ERROR_PREFIX = _COLOR + "4"


def _parse_styles(
    text: str,
) -> Iterator[tuple[str, int | None, int | None, bool, bool]]:
    style_regex = r"\x02|\x1f|\x03[0-9]{1,2}(?:,[0-9]{1,2})?|\x0f"

    # parts contains matched parts of the regex followed by texts
    # between those matched parts
    parts = [""] + re.split("(" + style_regex + ")", text)
    assert len(parts) % 2 == 0

    fg = None
    bg = None
    bold = False
    underline = False

    for style_spec, substring in zip(parts[0::2], parts[1::2]):
        if not style_spec:
            # beginning of text
            pass
        elif style_spec == _BOLD:
            bold = True
        elif style_spec == _UNDERLINE:
            underline = True
        elif style_spec.startswith(_COLOR):
            # _COLOR == '\x03'
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
        elif style_spec == _BACK_TO_NORMAL:
            fg = bg = None
            bold = underline = False
        else:
            raise ValueError("unexpected regex match: " + repr(style_spec))

        if substring:
            yield (substring, fg, bg, bold, underline)


# python's string hashes use a randomization by default, so hash('a')
# returns a different value after restarting python
def _nick_hash(nick: str) -> int:
    # http://www.cse.yorku.ca/~oz/hash.html
    hash_ = 5381
    for c in nick:
        hash_ = hash_ * 33 + ord(c)
    return hash_


def color_nick(nick: str) -> str:
    color = _NICK_COLORS[_nick_hash(nick) % len(_NICK_COLORS)]
    return _BOLD + _COLOR + str(color) + nick + _BACK_TO_NORMAL


class ColoredText(tkinter.Text):
    def __init__(self, master: tkinter.Misc, **kwargs: Any):
        kwargs.setdefault("fg", _MIRC_COLORS[0])
        kwargs.setdefault("bg", _MIRC_COLORS[1])
        super().__init__(master, **kwargs)

        # tags support underlining, but no bolding (lol)
        # TODO: allow custom font families and sizes
        this_font = tkfont.Font(name=self["font"], exists=True)
        self._bold_font = tkfont.Font(weight="bold")
        for key, value in this_font.actual().items():
            if key != "weight":
                self._bold_font[key] = value

        self.tag_configure("underline", underline=True)
        self.tag_configure("bold", font=self._bold_font)
        self.tag_configure("pinged", foreground=_MIRC_COLORS[9])
        for number, hexcolor in _MIRC_COLORS.items():
            self.tag_configure("foreground-%d" % number, foreground=hexcolor)
            self.tag_configure("background-%d" % number, background=hexcolor)
            self.tag_raise("foreground-%d" % number, "pinged")
            self.tag_raise("background-%d" % number, "pinged")

    def colored_insert(self, index: str, text: str, pinged: bool) -> None:
        """Like insert(), but interprets special color sequences correctly."""
        for substring, fg, bg, bold, underline in _parse_styles(text):
            tags = []
            if pinged:
                tags.append("pinged")
            if fg is not None:
                tags.append("foreground-%d" % fg)
            if bg is not None:
                tags.append("background-%d" % bg)
            if bold:
                tags.append("bold")
            if underline:
                tags.append("underline")
            self.insert(index, substring, tags)

    def nicky_insert(
        self, index: str, text: str, known_nicks: list[str], pinged: bool
    ) -> None:
        """Like colored_insert(), but colors nicks in known_nicks."""
        result_chars = list(text)
        matches = [
            match
            for match in re.finditer(backend.NICK_REGEX, text)
            if match.group(0) in known_nicks
        ]

        # do this backwards to prevent messing up indexes... you know
        for match in reversed(matches):
            nick = match.group(0)
            if nick in known_nicks:
                result_chars[match.start() : match.end()] = color_nick(nick)

        self.colored_insert(index, "".join(result_chars), pinged)
