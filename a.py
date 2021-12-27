from __future__ import annotations

import time
import tkinter
from tkinter import ttk


print(111)
root_window = tkinter.Tk()
print(222)
alice = ttk.PanedWindow(root_window, orient="horizontal")
print(333)

def _current_view_changed(event: object) -> None:
    print("A"*60)
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

    old_tags = set(view_selector.item(ServerView_view_id, "tags"))
    view_selector.item(
        ServerView_view_id, tags=list(old_tags - {"new_message", "pinged"})
    )
    _previous_view_id = view_id

print(444)
view_selector = ttk.Treeview(alice, show="tree", selectmode="browse")
print(555)
view_selector.tag_configure("pinged", foreground="#00ff00")
print(666)
view_selector.tag_configure("new_message", foreground="#ffcc66")
print(777)
alice.add(view_selector, weight=0)
print(888)
_contextmenu = tkinter.Menu(tearoff=False)
print(999)

_previous_view_id = None
view_selector.bind("<<TreeviewSelect>>", _current_view_changed)
print(1010)

middle_pane = ttk.Frame(alice)
print(2020)
alice.add(middle_pane, weight=1)
print(3030)

entry = tkinter.Entry(middle_pane)
entry.pack(side="bottom", fill="x")

print(4040)
ServerView_view_id = view_selector.insert("", "end", text="localhost")

print(5050)
ServerView_textwidget = tkinter.Text(
    alice,
    width=1,
    height=1,
    takefocus=True,
)

print(6060)
view_selector.item(ServerView_view_id, open=True)
print(7070)
view_selector.selection_set(ServerView_view_id)
print(8080)

alice.pack(fill="both", expand=True)
print(9090)

print(123123)
end = time.monotonic() + 5
print(456456)
while time.monotonic() < end:
    root_window.update()

print(50 * "W", flush=True)
root_window.destroy()
print(50 * "X", flush=True)
