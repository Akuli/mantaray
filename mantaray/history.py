from __future__ import annotations
import collections
import tkinter
from dataclasses import dataclass


@dataclass
class _HistoryItem:
    id: int
    entry_text: str


class History:
    def __init__(self, textwidget: tkinter.Text) -> None:
        self._id_counter = 0
        self._items = collections.deque([_HistoryItem(0, "")], maxlen=100)
        self._index = 0

        self._text_var = tkinter.StringVar()
        self._text_var.trace_add("write", self._update_history)
        self._textwidget = textwidget

    def _update_history(self, *junk: object) -> None:
        # Allow changing only the last (not yet enter pressed) item
        if self._index == len(self._items) - 1:
            self._items[-1].entry_text = self._text_var.get()

    def use_entry(self, entry: tkinter.Entry) -> None:
        entry.config(textvariable=self._text_var)
        entry.bind("<Up>", self.previous)
        entry.bind("<Down>", self.next)

    def _select_current_item(self) -> None:
        item = self._items[self._index]
        self._text_var.set(item.entry_text)

        self._textwidget.tag_remove("history-selection", "1.0", "end")
        try:
            self._textwidget.tag_add(
                "history-selection",
                f"history-start-{item.id}",
                f"history-end-{item.id}",
            )
        except tkinter.TclError as e:
            # No history-start-123 and history-end-123 marks.
            # This is typical for output of commands.
            pass

    def previous(self, junk_event: object = None) -> None:
        if self._index > 0:
            self._index -= 1
            self._select_current_item()

    def next(self, junk_event: object = None) -> None:
        if self._index + 1 < len(self._items):
            self._index += 1
            self._select_current_item()

    # Pressing up/down keys highlight text between marks
    # "history-start-123" and "history-end-123", if they
    # exist, where 123 is the returned history_id.
    def get_text_and_clear(self) -> tuple[str, int]:
        text = self._text_var.get()
        self._text_var.set("")
        self._items[-1].entry_text = text
        history_id = self._items[-1].id

        self._id_counter += 1
        self._items.append(_HistoryItem(self._id_counter, ""))
        self._index = len(self._items) - 1

        return (text, history_id)
