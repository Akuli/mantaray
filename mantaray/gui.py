# strongly inspired by xchat :)
# hexchat is a fork of xchat, so hexchat developers didn't invent this
from __future__ import annotations

import re
import sys
import tkinter
from functools import partial
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Any, Callable

from mantaray import commands, config, logs, textwidget_tags
from mantaray.views import ChannelView, PMView, ServerView, View


def _fix_tag_coloring_bug() -> None:
    # https://stackoverflow.com/a/60949800

    style = ttk.Style()

    def fixed_map(option: str) -> list[Any]:
        return [
            elm
            for elm in style.map("Treeview", query_opt=option)
            if elm[:2] != ("!disabled", "!selected")
        ]

    style.map(
        "Treeview",
        foreground=fixed_map("foreground"),
        background=fixed_map("background"),
    )


def ask_new_nick(parent: tkinter.Tk | tkinter.Toplevel, old_nick: str) -> str:
    dialog = tkinter.Toplevel()
    content = ttk.Frame(dialog)
    content.pack(fill="both", expand=True)

    ttk.Label(content, text="Enter a new nickname here:").place(
        relx=0.5, rely=0.1, anchor="center"
    )

    entry = ttk.Entry(content)
    entry.place(relx=0.5, rely=0.3, anchor="center")
    entry.insert(0, old_nick)

    ttk.Label(
        content,
        text="The same nick will be used on all channels.",
        justify="center",
        wraplength=150,
    ).place(relx=0.5, rely=0.6, anchor="center")

    buttonframe = ttk.Frame(content, borderwidth=5)
    buttonframe.place(relx=1.0, rely=1.0, anchor="se")

    result = old_nick

    def ok(junk_event: object = None) -> None:
        nonlocal result
        result = entry.get()
        dialog.destroy()

    ttk.Button(buttonframe, text="OK", command=ok).pack(side="left")
    ttk.Button(buttonframe, text="Cancel", command=dialog.destroy).pack(side="left")
    entry.bind("<Return>", (lambda junk_event: ok()))
    entry.bind("<Escape>", (lambda junk_event: dialog.destroy()))

    dialog.geometry("250x150")
    dialog.resizable(False, False)
    dialog.transient(parent)
    entry.focus()
    dialog.wait_window()

    return result


class IrcWidget(ttk.PanedWindow):
    def __init__(
        self,
        master: tkinter.Misc,
        settings: config.Settings,
        log_dir: Path,
        *,
        on_quit: Callable[[], None] | None = None,
        verbose: bool = False,
    ):
        super().__init__(master, orient="horizontal")
        self.settings = settings
        self.log_manager = logs.LogManager(log_dir)
        self.verbose = verbose
        self._on_quit = on_quit

        images_dir = Path(__file__).absolute().parent / "images"
        self.channel_image = tkinter.PhotoImage(
            file=(images_dir / "hashtagbubble-20x20.png")
        )
        self.pm_image = tkinter.PhotoImage(file=(images_dir / "face-20x20.png"))

        # Help Python's GC (tkinter images rely on __del__ and it sucks)
        self.bind(
            "<Destroy>", (lambda e: setattr(self, "channel_image", None)), add=True
        )
        self.bind("<Destroy>", (lambda e: setattr(self, "pm_image", None)), add=True)

        _fix_tag_coloring_bug()

        # https://stackoverflow.com/q/62824799
        self.tk.eval("ttk::style configure ViewSelector.Treeview -indent -5")
        self.view_selector = ttk.Treeview(
            self, show="tree", selectmode="browse", style="ViewSelector.Treeview"
        )
        self.view_selector.tag_configure("pinged", foreground="#00ff00")
        self.view_selector.tag_configure("new_message", foreground="#ffcc66")
        self.add(self.view_selector, weight=0)  # don't stretch
        self.contextmenu = tkinter.Menu(tearoff=False)

        self._previous_view: View | None = None
        self.view_selector.bind("<<TreeviewSelect>>", self._current_view_changed)

        if sys.platform == "darwin":
            self.view_selector.bind("<Button-2>", self.on_view_selector_right_click)
            self.view_selector.bind(
                "<Control-Button-1>", self.on_view_selector_right_click
            )
        else:
            self.view_selector.bind("<Button-3>", self.on_view_selector_right_click)

        self.textwidget_container = ttk.Frame(self)
        self.add(self.textwidget_container, weight=1)  # always stretch

        entryframe = ttk.Frame(self.textwidget_container)
        entryframe.pack(side="bottom", fill="x")

        # TODO: add a tooltip to the button, it's not very obvious
        self.nickbutton = ttk.Button(entryframe, command=self._show_change_nick_dialog)
        self.nickbutton.pack(side="left")

        self.entry = tkinter.Entry(
            entryframe,
            font=self.settings.font,
            fg=textwidget_tags.FOREGROUND,
            bg=textwidget_tags.BACKGROUND,
            insertbackground=textwidget_tags.FOREGROUND,
        )
        self.entry.pack(side="left", fill="both", expand=True)
        self.entry.bind("<Return>", self.on_enter_pressed)
        self.entry.bind("<Tab>", self._tab_event_handler)
        self.entry.bind("<Prior>", self._scroll_up)
        self.entry.bind("<Next>", self._scroll_down)

        self.views_by_id: dict[str, View] = {}
        for server_config in self.settings.servers:
            self._create_and_add_server_view(server_config)

    def get_current_view(self) -> View:
        [view_id] = self.view_selector.selection()
        return self.views_by_id[view_id]

    # for tests
    def text(self) -> str:
        return self.get_current_view().textwidget.get("1.0", "end - 1 char")

    def get_server_views(self) -> list[ServerView]:
        server_views = [
            v for v in self.views_by_id.values() if isinstance(v, ServerView)
        ]
        server_views.sort(key=(lambda v: self.view_selector.index(v.view_id)))
        return server_views

    def _show_change_nick_dialog(self) -> None:
        server_view = self.get_current_view().server_view
        new_nick = ask_new_nick(self.winfo_toplevel(), server_view.core.nick)
        if new_nick != server_view.core.nick:
            server_view.core.send(f"NICK {new_nick}")
            server_view.settings.nick = new_nick

    def on_enter_pressed(self, junk_event: object = None) -> None:
        view = self.get_current_view()
        entry_text, history_id = view.history.get_text_and_clear_entry()
        commands.handle_command(view, view.server_view.core, entry_text, history_id)

    def _scroll_up(self, junk_event: object) -> None:
        self.get_current_view().textwidget.yview_scroll(-1, "pages")

    def _scroll_down(self, junk_event: object) -> None:
        self.get_current_view().textwidget.yview_scroll(1, "pages")

    def bigger_font_size(self) -> None:
        self.settings.font["size"] += 1
        self.settings.save()

    def smaller_font_size(self) -> None:
        if self.settings.font["size"] > 3:
            self.settings.font["size"] -= 1
            self.settings.save()

    def _get_flat_list_of_item_ids(self) -> list[str]:
        result = []
        for server_id in self.view_selector.get_children(""):
            result.append(server_id)
            result.extend(self.view_selector.get_children(server_id))
        return result

    def select_by_number(self, index: int) -> None:
        ids = self._get_flat_list_of_item_ids()
        try:
            self.view_selector.selection_set(ids[index])
        except IndexError:
            pass

    def select_previous_view(self) -> None:
        ids = self._get_flat_list_of_item_ids()
        index = ids.index(self.get_current_view().view_id) - 1
        if index >= 0:
            self.view_selector.selection_set(ids[index])

    def select_next_view(self) -> None:
        ids = self._get_flat_list_of_item_ids()
        index = ids.index(self.get_current_view().view_id) + 1
        if index < len(ids):
            self.view_selector.selection_set(ids[index])

    def _select_another_view(self, bad_view: View) -> None:
        if self.get_current_view() == bad_view:
            ids = self._get_flat_list_of_item_ids()
            index = ids.index(bad_view.view_id)
            if index == 0:
                self.view_selector.selection_set(ids[1])
            else:
                self.view_selector.selection_set(ids[index - 1])

    def move_view_up(self) -> None:
        view_id = self.get_current_view().view_id
        self.view_selector.move(
            view_id,
            self.view_selector.parent(view_id),
            self.view_selector.index(view_id) - 1,
        )
        self.sort_settings_according_to_gui()

    def move_view_down(self) -> None:
        view_id = self.get_current_view().view_id
        self.view_selector.move(
            view_id,
            self.view_selector.parent(view_id),
            self.view_selector.index(view_id) + 1,
        )
        self.sort_settings_according_to_gui()

    def sort_settings_according_to_gui(self) -> None:
        servers_in_gui = [view.settings for view in self.get_server_views()]
        self.settings.servers.sort(key=servers_in_gui.index)

        for server_view in self.get_server_views():
            channels_in_gui = [
                view.channel_name
                for view in server_view.get_subviews()
                if isinstance(view, ChannelView)
            ]
            server_view.settings.joined_channels.sort(
                key=(
                    lambda c: (
                        channels_in_gui.index(c) if c in channels_in_gui else 1000000
                    )
                )
            )

        self.settings.save()

    def _tab_event_handler(self, junk_event: object) -> str:
        self.autocomplete()
        return "break"

    # TODO: shift+tab = backwards ?
    def autocomplete(self) -> None:
        view = self.get_current_view()
        if not isinstance(view, ChannelView):
            return

        cursor_pos = self.entry.index("insert")
        match = re.fullmatch(r"(.*\s)?([^\s:]+):? ?", self.entry.get()[:cursor_pos])
        if match is None:
            return
        preceding_text, last_word = match.groups()  # preceding_text can be None

        nicks = view.userlist.get_nicks()
        if last_word in nicks:
            completion = nicks[(nicks.index(last_word) + 1) % len(nicks)]
        else:
            try:
                completion = next(
                    username
                    for username in nicks
                    if username.lower().startswith(last_word.lower())
                )
            except StopIteration:
                return

        if preceding_text:
            new_text = preceding_text + completion + " "
        else:
            new_text = completion + ": "
        self.entry.delete(0, cursor_pos)
        self.entry.insert(0, new_text)
        self.entry.icursor(len(new_text))

    def _current_view_changed(self, event: object) -> None:
        new_view = self.get_current_view()
        if self._previous_view == new_view:
            return

        if (
            isinstance(self._previous_view, ChannelView)
            and self._previous_view.userlist.treeview.winfo_exists()
        ):
            self.remove(self._previous_view.userlist.treeview)
        if isinstance(new_view, ChannelView):
            self.add(new_view.userlist.treeview, weight=0)

        if (
            self._previous_view is not None
            and self._previous_view.textwidget.winfo_exists()
        ):
            self._previous_view.textwidget.pack_forget()
        new_view.textwidget.pack(side="top", fill="both", expand=True)
        new_view.mark_seen()
        new_view.history.use_entry(self.entry)

        self._previous_view = new_view

        self.nickbutton.config(text=new_view.server_view.core.nick)

    def add_view(self, view: View) -> None:
        assert view.view_id not in self.views_by_id
        self.view_selector.item(view.server_view.view_id, open=True)
        self.views_by_id[view.view_id] = view
        self.view_selector.selection_set(view.view_id)

    def _create_and_add_server_view(self, settings: config.ServerSettings) -> None:
        view = ServerView(self, settings)
        self.add_view(view)
        view.start_running()  # Must be after add_view()

    def remove_view(self, view: ChannelView | PMView) -> None:
        self._select_another_view(view)
        self.view_selector.delete(view.view_id)
        view.close_log_file()
        view.destroy_widgets()
        del self.views_by_id[view.view_id]

    # Does not remove the server from settings, so mantaray will connect
    # to it when started next time.
    #
    # This should be called only when exiting mantaray. It breaks the
    # sort_settings_according_to_gui() method, for example.
    def remove_server(self, server_view: ServerView) -> None:
        for subview in server_view.get_subviews():
            assert isinstance(subview, (ChannelView, PMView))
            self.remove_view(subview)

        is_last = len(self.view_selector.get_children("")) == 1
        if not is_last:
            self._select_another_view(server_view)

        self.view_selector.delete(server_view.view_id)
        server_view.close_log_file()
        server_view.destroy_widgets()
        del self.views_by_id[server_view.view_id]

        if is_last:
            if self._on_quit is None:
                self.destroy()
            else:
                self._on_quit()

    def _show_add_server_dialog(self) -> None:
        server_settings = config.ServerSettings()
        user_clicked_connect = config.show_connection_settings_dialog(
            transient_to=self.winfo_toplevel(),
            settings=server_settings,
            connecting_to_new_server=True,
        )
        if user_clicked_connect:
            self.settings.add_server(server_settings)
            self._create_and_add_server_view(server_settings)

    def _leave_server(self, view: ServerView) -> None:
        wont_remember = []
        wont_remember.append(
            f"the host and port ({view.settings.host} {view.settings.port})"
        )
        wont_remember.append(f"your nick ({view.settings.nick})")
        if view.settings.username != view.settings.nick:
            wont_remember.append(f"your username ({view.settings.username})")
        if view.settings.password is not None:
            wont_remember.append("your password")
        wont_remember.append("what channels you joined")

        if messagebox.askyesno(
            "Leave server",
            f"Are you sure you want to leave {view.settings.host}?",
            detail=(
                f"You can reconnect later, but if you decide to do so,"
                f" Mantaray won't remember things like"
                f" {', '.join(wont_remember[:-1])} or {wont_remember[-1]}."
            ),
            default="no",
        ):
            view.core.quit()
            self.settings.servers.remove(view.settings)
            self.settings.save()

    def _fill_menu_for_server(self, view: ServerView | None) -> None:
        if view is not None:
            self.contextmenu.add_command(
                label="Server settings...", command=view.show_config_dialog
            )
            self.contextmenu.add_command(
                label="Leave this server",
                command=partial(self._leave_server, view),
                # To leave the last server, you need to close window instead
                state=("disabled" if len(self.get_server_views()) == 1 else "normal"),
            )

        self.contextmenu.add_command(
            label="Connect to a new server...", command=self._show_add_server_dialog
        )

    def _fill_menu_for_channel(self, view: ChannelView) -> None:
        def toggle_autojoin(*junk: object) -> None:
            if view.channel_name in view.server_view.settings.joined_channels:
                view.server_view.settings.joined_channels.remove(view.channel_name)
            else:
                view.server_view.settings.joined_channels.append(view.channel_name)
                self.sort_settings_according_to_gui()
            view.server_view.settings.save()

        def toggle_extra_notifications(*junk: object) -> None:
            view.server_view.settings.extra_notifications ^= {view.channel_name}
            view.server_view.settings.save()

        autojoin_var = tkinter.BooleanVar(
            value=(view.channel_name in view.server_view.settings.joined_channels)
        )
        extra_notif_var = tkinter.BooleanVar(
            value=(view.channel_name in view.server_view.settings.extra_notifications)
        )

        autojoin_var.trace_add("write", toggle_autojoin)
        extra_notif_var.trace_add("write", toggle_extra_notifications)

        self.contextmenu.add_checkbutton(
            label="Join when Mantaray starts", variable=autojoin_var
        )
        self.contextmenu.add_checkbutton(
            label="Show notifications for all messages", variable=extra_notif_var
        )

        self._garbage_collection_is_lol = (autojoin_var, extra_notif_var)

        self.contextmenu.add_command(
            label="Part this channel",
            command=(lambda: view.server_view.core.send(f"PART {view.channel_name}")),
        )

    def _fill_menu_for_pm(self, view: PMView) -> None:
        self.contextmenu.add_command(
            label="Close", command=(lambda: self.remove_view(view))
        )

    def on_view_selector_right_click(
        self, event: tkinter.Event[tkinter.ttk.Treeview]
    ) -> None:
        item_id = self.view_selector.identify_row(event.y)
        if item_id:
            self.view_selector.selection_set(item_id)
            view = self.get_current_view()
        else:
            view = None

        self.contextmenu.delete(0, "end")

        if view is None or isinstance(view, ServerView):
            self._fill_menu_for_server(view)
        if isinstance(view, ChannelView):
            self._fill_menu_for_channel(view)
        if isinstance(view, PMView):
            self._fill_menu_for_pm(view)

        self.contextmenu.tk_popup(event.x_root + 5, event.y_root)
