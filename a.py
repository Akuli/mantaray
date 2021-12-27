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

root_window.update()
root_window.destroy()
