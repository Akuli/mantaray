from __future__ import annotations
import collections
import tkinter
from dataclasses import dataclass


@dataclass
class _HistoryItem:
    id: int
    entry_text: str


class History:
    def __init__(self) -> None:
        self._id_counter = 0
        self._items = collections.deque([_HistoryItem(0, "")], maxlen=100)
        self._index = 0

        self._text_var = tkinter.StringVar()
        self._text_var.trace_add("write", self._update_history)

    def _update_history(self, *junk: object) -> None:
        # Allow changing only the last (not yet enter pressed) item
        if self._index == len(self._items) - 1:
            self._items[-1].entry_text = self._text_var.get()

    def use_entry(self, entry: tkinter.Entry) -> None:
        entry.config(textvariable=self._text_var)
        entry.bind("<Up>", self.previous)
        entry.bind("<Down>", self.next)

    def previous(self, junk_event: object = None) -> None:
        if self._index > 0:
            self._index -= 1
            self._text_var.set(self._items[self._index].entry_text)

    def next(self, junk_event: object = None) -> None:
        if self._index + 1 < len(self._items):
            self._index += 1
            self._text_var.set(self._items[self._index].entry_text)

    def get_text_and_clear(self) -> str:
        text = self._text_var.get()
        self._text_var.set("")
        self._items[-1].entry_text = text

        self._id_counter += 1
        self._items.append(_HistoryItem(self._id_counter, ""))
        self._index = len(self._items) - 1

        return text
