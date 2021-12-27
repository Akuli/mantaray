# strongly inspired by xchat :)
# hexchat is a fork of xchat, so hexchat developers didn't invent this
from __future__ import annotations
import sys
import tkinter
from tkinter import ttk
from tkinter.font import Font
from pathlib import Path

from mantaray import config, colors
from mantaray.views import View, ServerView, ChannelView, PMView


class IrcWidget(ttk.PanedWindow):
    def __init__(self, master: tkinter.Misc, file_config: config.Config, log_dir: Path):
        super().__init__(master, orient="horizontal")
        self.log_dir = log_dir

        self.font = Font(
            family=file_config["font_family"], size=file_config["font_size"]
        )
        if not self.font.metrics("fixed"):
            self.font.config(family=config.get_default_fixed_font()[0])

        self.view_selector = ttk.Treeview(self, show="tree", selectmode="browse")
        self.view_selector.tag_configure("pinged", foreground="#00ff00")
        self.view_selector.tag_configure("new_message", foreground="#ffcc66")
        self.add(self.view_selector, weight=0)  # don't stretch
        self._contextmenu = tkinter.Menu(tearoff=False)

        self._previous_view: View | None = None
        self.view_selector.bind("<<TreeviewSelect>>", self._current_view_changed)

        self._middle_pane = ttk.Frame(self)
        self.add(self._middle_pane, weight=1)  # always stretch

        entryframe = ttk.Frame(self._middle_pane)
        entryframe.pack(side="bottom", fill="x")

        self.entry = tkinter.Entry(
            entryframe,
            font=self.font,
            fg=colors.FOREGROUND,
            bg=colors.BACKGROUND,
            insertbackground=colors.FOREGROUND,
        )
        self.entry.pack(side="left", fill="both", expand=True)

        # {channel_like.name: channel_like}
        self.views_by_id: dict[str, View] = {}
        for server_config in file_config["servers"]:
            self.add_view(ServerView(self, server_config))

    def get_current_view(self) -> View:
        [view_id] = self.view_selector.selection()
        return self.views_by_id[view_id]

    def get_server_views(self) -> list[ServerView]:
        result = []
        for view_id in self.view_selector.get_children(""):
            view = self.views_by_id[view_id]
            assert isinstance(view, ServerView)
            result.append(view)
        return result

    def _current_view_changed(self, event: object) -> None:
        new_view = self.get_current_view()
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
        new_view.mark_seen()

        self._previous_view = new_view

    def add_view(self, view: View) -> None:
        assert view.view_id not in self.views_by_id
        self.view_selector.item(view.server_view.view_id, open=True)
        self.views_by_id[view.view_id] = view
        self.view_selector.selection_set(view.view_id)
