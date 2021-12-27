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

        self.textwidget = tkinter.Text(
            irc_widget,
            width=1,
            height=1,
            font=irc_widget.font,
            state="disabled",
            takefocus=True,
        )
        self.textwidget.bind("<Button-1>", (lambda e: self.textwidget.focus()))

        self.textwidget.tag_configure("underline", underline=True)
        self.textwidget.tag_configure("pinged", foreground="black")
        self.textwidget.tag_configure("error", foreground="black")
        self.textwidget.tag_configure("info", foreground="red")
        self.textwidget.tag_configure("history-selection", background="red")
        self.textwidget.tag_configure("channel", foreground="red")
        self.textwidget.tag_configure("self-nick", foreground="red", underline=True)
        self.textwidget.tag_configure("other-nick", foreground="red", underline=True)

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
        if show_in_gui:
            do_the_scroll = self.textwidget.yview()[1] == 1.0
            padding = " " * (16 - len(sender))

            if sender == "*":
                sender_tags = []
            elif sender == "Alice":
                sender_tags = ["self-nick"]
            else:
                sender_tags = ["other-nick"]

            self.textwidget.config(state="normal")
            start = self.textwidget.index("end - 1 char")
            self.textwidget.insert("end", time.strftime("[%H:%M]") + " " + padding)
            self.textwidget.insert("end", sender, sender_tags)
            self.textwidget.insert("end", " | ")
            flatten = itertools.chain.from_iterable
            if chunks:
                self.textwidget.insert("end", *flatten(chunks))
            self.textwidget.insert("end", "\n")
            if pinged:
                self.textwidget.tag_add("pinged", start, "end - 1 char")
            self.textwidget.config(state="disabled")

            if do_the_scroll:
                self.textwidget.see("end")

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
        view = ServerView(self)
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
