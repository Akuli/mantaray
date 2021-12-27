from __future__ import annotations

import itertools
import queue
import time
import tkinter
from tkinter import ttk
from tkinter.font import Font


def ServerView_handle_events() -> None:
    alice.after(100, ServerView_handle_events)

    while True:
        try:
            message = ServerView_event_queue.get(block=False)
        except queue.Empty:
            break

        assert not view_selector.get_children(ServerView_view_id)
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


root_window = tkinter.Tk()
alice = ttk.PanedWindow(root_window, orient="horizontal")

def _current_view_changed(event: object) -> None:
    global _previous_view_id
    view_id = ServerView_view_id
    if _previous_view_id == view_id:
        return

    if (
        _previous_view_id is not None
        and ServerView_textwidget.winfo_exists()
    ):
        ServerView_textwidget.pack_forget()
    ServerView_textwidget.pack(
        in_=_middle_pane, side="top", fill="both", expand=True
    )
    global ServerView_notification_count
    ServerView_notification_count = 0
    alice.event_generate("<<NotificationCountChanged>>")

    old_tags = set(view_selector.item(ServerView_view_id, "tags"))
    view_selector.item(
        ServerView_view_id, tags=list(old_tags - {"new_message", "pinged"})
    )

    _previous_view_id = view_id

font = Font()

view_selector = ttk.Treeview(alice, show="tree", selectmode="browse")
view_selector.tag_configure("pinged", foreground="#00ff00")
view_selector.tag_configure("new_message", foreground="#ffcc66")
alice.add(view_selector, weight=0)
_contextmenu = tkinter.Menu(tearoff=False)

_previous_view_id = None
view_selector.bind("<<TreeviewSelect>>", _current_view_changed)

_middle_pane = ttk.Frame(alice)
alice.add(_middle_pane, weight=1)

entryframe = ttk.Frame(_middle_pane)
entryframe.pack(side="bottom", fill="x")

entry = tkinter.Entry(
    entryframe,
    font=font,
)
entry.pack(side="left", fill="both", expand=True)

ServerView_view_id = view_selector.insert("", "end", text="localhost")
ServerView_notification_count = 0

ServerView_textwidget = tkinter.Text(
    alice,
    width=1,
    height=1,
    font=font,
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
view_selector.item(ServerView_view_id, open=True)
view_selector.selection_set(ServerView_view_id)

alice.pack(fill="both", expand=True)

end = time.monotonic() + 5
while time.monotonic() < end:
    root_window.update()

print(50 * "W", flush=True)
root_window.destroy()
print(50 * "X", flush=True)
