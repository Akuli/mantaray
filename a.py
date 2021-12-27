from __future__ import annotations

import collections
import itertools
import queue
import socket
import ssl
import threading
import time
import tkinter
import traceback
from tkinter import ttk
from tkinter.font import Font


class IrcCore:

    def __init__(self):
        self.event_queue = queue.Queue()
        self._threads: list[threading.Thread] = []
        self._quit_event = threading.Event()

    def start_threads(self) -> None:
        assert not self._threads
        self._threads.append(threading.Thread(target=self._connect_and_recv_loop))
        for thread in self._threads:
            thread.start()

    def _connect_and_recv_loop(self) -> None:
        self.event_queue.put("Blah...")


class ServerView:
    def __init__(self, irc_widget):
        self.irc_widget = irc_widget
        self.view_id = irc_widget.view_selector.insert("", "end", text="localhost")
        self.notification_count = 0

        self.core = IrcCore()
        self.extra_notifications = set()
        self._join_leave_hiding_config = {"show_by_default": True, "exception_nicks": []}

        self.core.start_threads()
        self.handle_events()

    def mark_seen(self) -> None:
        self.notification_count = 0
        self.irc_widget.event_generate("<<NotificationCountChanged>>")

        old_tags = set(self.irc_widget.view_selector.item(self.view_id, "tags"))
        self.irc_widget.view_selector.item(
            self.view_id, tags=list(old_tags - {"new_message", "pinged"})
        )

    def add_message(
        self,
        sender: str,
        *chunks: tuple[str, list[str]],
        pinged: bool = False,
        show_in_gui: bool = True,
    ) -> None:
        pass

    def on_connectivity_message(self, message: str, *, error: bool = False) -> None:
        self.add_message("", (message, ["error" if error else "info"]))

    def get_subviews(self, *, include_server: bool = False):
        result = []
        if include_server:
            result.append(self)
        for view_id in self.irc_widget.view_selector.get_children(self.view_id):
            result.append(self.irc_widget.views_by_id[view_id])
        return result

    def handle_events(self) -> None:
        self.irc_widget.after(100, self.handle_events)

        while True:
            try:
                event = self.core.event_queue.get(block=False)
            except queue.Empty:
                break

            for view in self.get_subviews(include_server=True):
                view.on_connectivity_message(event)


class IrcWidget(ttk.PanedWindow):
    def __init__(self, master: tkinter.Misc):
        super().__init__(master, orient="horizontal")
        self.font = Font()

        self.view_selector = ttk.Treeview(self, show="tree", selectmode="browse")
        self.view_selector.tag_configure("pinged", foreground="#00ff00")
        self.view_selector.tag_configure("new_message", foreground="#ffcc66")
        self.add(self.view_selector, weight=0)
        self._contextmenu = tkinter.Menu(tearoff=False)

        self._previous_view = None
        self.view_selector.bind("<<TreeviewSelect>>", self._current_view_changed)

        self._middle_pane = ttk.Frame(self)
        self.add(self._middle_pane, weight=1)

        entryframe = ttk.Frame(self._middle_pane)
        entryframe.pack(side="bottom", fill="x")

        self.entry = tkinter.Entry(
            entryframe,
            font=self.font,
        )
        self.entry.pack(side="left", fill="both", expand=True)

        self.views_by_id = {}
        self.add_view(ServerView(self))

    def get_current_view(self):
        [view_id] = self.view_selector.selection()
        return self.views_by_id[view_id]

    def _current_view_changed(self, event: object) -> None:
        new_view = self.get_current_view()
        if self._previous_view == new_view:
            return

        new_view.mark_seen()

        self._previous_view = new_view

    def add_view(self, view) -> None:
        assert view.view_id not in self.views_by_id
        self.view_selector.item(view.view_id, open=True)
        self.views_by_id[view.view_id] = view
        self.view_selector.selection_set(view.view_id)


root_window = tkinter.Tk()
alice = IrcWidget(
    root_window,
)
alice.pack(fill="both", expand=True)

end = time.monotonic() + 5
while time.monotonic() < end:
    root_window.update()

print(50 * "W", flush=True)
root_window.destroy()
print(50 * "X", flush=True)
