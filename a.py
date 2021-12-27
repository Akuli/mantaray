from __future__ import annotations

import time
import tkinter
from tkinter import ttk


root_window = tkinter.Tk()
alice = ttk.PanedWindow(root_window, orient="horizontal")

view_selector = ttk.Treeview(alice, show="tree", selectmode="browse")
alice.add(view_selector, weight=0)
_contextmenu = tkinter.Menu(tearoff=False)

middle_pane = ttk.Frame(alice)
alice.add(middle_pane, weight=1)

entry = tkinter.Entry(middle_pane)
entry.pack(side="bottom", fill="x")

view_id = view_selector.insert("", "end", text="localhost")

view_selector.item(view_id, open=True)
view_selector.selection_set(view_id)

alice.pack(fill="both", expand=True)

tkinter.Text(alice).pack(in_=middle_pane)

end = time.monotonic() + 5
while time.monotonic() < end:
    root_window.update()

print(50 * "W", flush=True)
root_window.destroy()
print(50 * "X", flush=True)
