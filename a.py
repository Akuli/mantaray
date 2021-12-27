from __future__ import annotations

import itertools
import queue
import time
import tkinter
from tkinter import ttk
from tkinter.font import Font


def ServerView__init__(irc_widget):
    global ServerView_irc_widget
    global ServerView_view_id
    global ServerView_notification_count
    global ServerView_textwidget
    global ServerView_event_queue
    global ServerView_extra_notifications
    global ServerView__join_leave_hiding_config
    ServerView_irc_widget = irc_widget
    ServerView_view_id = irc_widget.view_selector.insert("", "end", text="localhost")
    ServerView_notification_count = 0

    ServerView_textwidget = tkinter.Text(
        irc_widget,
        width=1,
        height=1,
        font=irc_widget.font,
        state="disabled",
        takefocus=True,
    )
    ServerView_textwidget.bind("<Button-1>", (lambda e: ServerView_textwidget.focus()))

    ServerView_textwidget.tag_configure("underline", underline=True)
    ServerView_textwidget.tag_configure("pinged", foreground="black")
    ServerView_textwidget.tag_configure("error", foreground="black")
    ServerView_textwidget.tag_configure("info", foreground="red")
    ServerView_textwidget.tag_configure("history-selection", background="red")
    ServerView_textwidget.tag_configure("channel", foreground="red")
    ServerView_textwidget.tag_configure("self-nick", foreground="red", underline=True)
    ServerView_textwidget.tag_configure("other-nick", foreground="red", underline=True)

    ServerView_event_queue = queue.Queue()
    ServerView_extra_notifications = set()
    ServerView__join_leave_hiding_config = {"show_by_default": True, "exception_nicks": []}

    ServerView_event_queue.put("Blah...")
    ServerView_handle_events()


def ServerView_handle_events() -> None:
    ServerView_irc_widget.after(100, ServerView_handle_events)

    while True:
        try:
            message = ServerView_event_queue.get(block=False)
        except queue.Empty:
            break

        assert not ServerView_irc_widget.view_selector.get_children(ServerView_view_id)
        sender = ""
        chunks = ((message, ["info"]),)
        do_the_scroll = ServerView_textwidget.yview()[1] == 1.0
        padding = " " * (16 - len(sender))

        if sender == "*":
            sender_tags = []
        elif sender == "Alice":
            sender_tags = ["self-nick"]
        else:
            sender_tags = ["other-nick"]

        ServerView_textwidget.config(state="normal")
        ServerView_textwidget.insert("end", time.strftime("[%H:%M]") + " " + padding)
        ServerView_textwidget.insert("end", sender, sender_tags)
        ServerView_textwidget.insert("end", " | ")
        flatten = itertools.chain.from_iterable
        if chunks:
            ServerView_textwidget.insert("end", *flatten(chunks))
        ServerView_textwidget.insert("end", "\n")
        ServerView_textwidget.config(state="disabled")

        if do_the_scroll:
            ServerView_textwidget.see("end")


class IrcWidget(ttk.PanedWindow):
    def __init__(self, master: tkinter.Misc):
        super().__init__(master, orient="horizontal")
        self.font = Font()

        self.view_selector = ttk.Treeview(self, show="tree", selectmode="browse")
        self.view_selector.tag_configure("pinged", foreground="#00ff00")
        self.view_selector.tag_configure("new_message", foreground="#ffcc66")
        self.add(self.view_selector, weight=0)
        self._contextmenu = tkinter.Menu(tearoff=False)

        self._previous_view_id = None
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

        ServerView__init__(self)
        self.view_selector.item(ServerView_view_id, open=True)
        self.view_selector.selection_set(ServerView_view_id)

    def _current_view_changed(self, event: object) -> None:
        view_id = ServerView_view_id
        if self._previous_view_id == view_id:
            return

        if (
            self._previous_view_id is not None
            and ServerView_textwidget.winfo_exists()
        ):
            ServerView_textwidget.pack_forget()
        ServerView_textwidget.pack(
            in_=self._middle_pane, side="top", fill="both", expand=True
        )
        global ServerView_notification_count
        ServerView_notification_count = 0
        ServerView_irc_widget.event_generate("<<NotificationCountChanged>>")

        old_tags = set(ServerView_irc_widget.view_selector.item(ServerView_view_id, "tags"))
        ServerView_irc_widget.view_selector.item(
            ServerView_view_id, tags=list(old_tags - {"new_message", "pinged"})
        )

        self._previous_view_id = view_id


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
