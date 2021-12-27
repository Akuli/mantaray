from __future__ import annotations

import time
import tkinter
from tkinter import ttk


root_window = tkinter.Tk()
alice = ttk.PanedWindow(root_window)

view_selector = ttk.Treeview(alice)
alice.add(view_selector)
_contextmenu = tkinter.Menu()

middle_pane = ttk.Frame(alice)
alice.add(middle_pane)

entry = tkinter.Entry(middle_pane)
entry.pack()

view_id = view_selector.insert("", "end")

view_selector.item(view_id, open=True)
view_selector.selection_set(view_id)

alice.pack()

tkinter.Text(alice).pack(in_=middle_pane)

end = time.monotonic() + 5
while time.monotonic() < end:
    root_window.update()

print(50 * "W", flush=True)
root_window.destroy()
print(50 * "X", flush=True)
