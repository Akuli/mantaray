from __future__ import annotations

import time
import tkinter
from tkinter import ttk


root_window = tkinter.Tk()
panedwindow = ttk.PanedWindow(root_window)

treeview = ttk.Treeview(panedwindow)
treeview.insert("", "end")
panedwindow.add(treeview)

middle_pane = ttk.Frame(panedwindow)
panedwindow.add(middle_pane)
tkinter.Entry(middle_pane).pack()
panedwindow.pack()

tkinter.Text(panedwindow).pack(in_=middle_pane)

end = time.monotonic() + 1
while time.monotonic() < end:
    root_window.update()

print(50 * "W", flush=True)
root_window.destroy()
print(50 * "X", flush=True)
