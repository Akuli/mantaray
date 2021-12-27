from __future__ import annotations

import time
import tkinter
from tkinter import ttk


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
        in_=middle_pane, side="top", fill="both", expand=True
    )
    alice.event_generate("<<NotificationCountChanged>>")

    old_tags = set(view_selector.item(ServerView_view_id, "tags"))
    view_selector.item(
        ServerView_view_id, tags=list(old_tags - {"new_message", "pinged"})
    )

    _previous_view_id = view_id

view_selector = ttk.Treeview(alice, show="tree", selectmode="browse")
view_selector.tag_configure("pinged", foreground="#00ff00")
view_selector.tag_configure("new_message", foreground="#ffcc66")
alice.add(view_selector, weight=0)
_contextmenu = tkinter.Menu(tearoff=False)

_previous_view_id = None
view_selector.bind("<<TreeviewSelect>>", _current_view_changed)

middle_pane = ttk.Frame(alice)
alice.add(middle_pane, weight=1)

entry = tkinter.Entry(middle_pane)
entry.pack(side="bottom", fill="x")

ServerView_view_id = view_selector.insert("", "end", text="localhost")

ServerView_textwidget = tkinter.Text(
    alice,
    width=1,
    height=1,
    takefocus=True,
)

view_selector.item(ServerView_view_id, open=True)
view_selector.selection_set(ServerView_view_id)

alice.pack(fill="both", expand=True)

end = time.monotonic() + 5
while time.monotonic() < end:
    root_window.update()

print(50 * "W", flush=True)
root_window.destroy()
print(50 * "X", flush=True)
