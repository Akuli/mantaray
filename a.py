from __future__ import annotations

import itertools
import queue
import time
import tkinter
from tkinter import ttk
from tkinter.font import Font


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

        self.event_queue = queue.Queue()
        self.extra_notifications = set()
        self._join_leave_hiding_config = {"show_by_default": True, "exception_nicks": []}

        self.event_queue.put("Blah...")
        self.handle_events()

    def handle_events(self) -> None:
        self.irc_widget.after(100, self.handle_events)

        while True:
            try:
                message = self.event_queue.get(block=False)
            except queue.Empty:
                break

            assert not self.irc_widget.view_selector.get_children(self.view_id)
            sender = ""
            chunks = ((message, ["info"]),)
            do_the_scroll = self.textwidget.yview()[1] == 1.0
            padding = " " * (16 - len(sender))

            if sender == "*":
                sender_tags = []
            elif sender == "Alice":
                sender_tags = ["self-nick"]
            else:
                sender_tags = ["other-nick"]

            self.textwidget.config(state="normal")
            self.textwidget.insert("end", time.strftime("[%H:%M]") + " " + padding)
            self.textwidget.insert("end", sender, sender_tags)
            self.textwidget.insert("end", " | ")
            flatten = itertools.chain.from_iterable
            if chunks:
                self.textwidget.insert("end", *flatten(chunks))
            self.textwidget.insert("end", "\n")
            self.textwidget.config(state="disabled")

            if do_the_scroll:
                self.textwidget.see("end")


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

        view = ServerView(self)
        self.views_by_id = {view.view_id: view}
        self.view_selector.item(view.view_id, open=True)
        self.view_selector.selection_set(view.view_id)

    def _current_view_changed(self, event: object) -> None:
        [view_id] = self.view_selector.selection()
        new_view = self.views_by_id[view_id]
        if self._previous_view == new_view:
            return

        if (
            self._previous_view is not None
            and self._previous_view.textwidget.winfo_exists()
        ):
            self._previous_view.textwidget.pack_forget()
        new_view.textwidget.pack(
            in_=self._middle_pane, side="top", fill="both", expand=True
        )
        new_view.notification_count = 0
        new_view.irc_widget.event_generate("<<NotificationCountChanged>>")

        old_tags = set(new_view.irc_widget.view_selector.item(new_view.view_id, "tags"))
        new_view.irc_widget.view_selector.item(
            new_view.view_id, tags=list(old_tags - {"new_message", "pinged"})
        )

        self._previous_view = new_view


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
