# strongly inspired by xchat :)
# hexchat is a fork of xchat, so hexchat developers didn't invent this
from __future__ import annotations
import os
import queue
import re
import subprocess
import sys
import tkinter
import traceback
from tkinter import ttk
from typing import Callable, Any

from irc_client import backend, config
from irc_client.views import View, ServerView, ChannelView, PMView


def _show_popup(title: str, text: str) -> None:
    try:
        if sys.platform == "win32":
            print("Sorry, no popups on windows yet :(")  # FIXME
        elif sys.platform == "darwin":
            # https://stackoverflow.com/a/41318195
            command = (
                "on run argv\n"
                "  display notification (item 2 of argv) with title (item 1 of argv)\n"
                "end run\n"
            )
            subprocess.call(["osascript", "-e", command, title, text])
        else:
            subprocess.call(["notify-send", f"[{title}] {text}"])
    except OSError:
        traceback.print_exc()


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
        server_config: config.ServerConfig,
        on_quit: Callable[[], object] | None = None,
    ):
        super().__init__(master, orient="horizontal")
        self.core = backend.IrcCore(server_config)
        self.core.start()

        self._extra_notifications = set(server_config["extra_notifications"])

        self._on_quit = on_quit

        images_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "images")
        self.channel_image = tkinter.PhotoImage(
            file=os.path.join(images_dir, "hashtagbubble-20x20.png")
        )
        self.pm_image = tkinter.PhotoImage(
            file=os.path.join(images_dir, "face-20x20.png")
        )

        # Help Python's GC (tkinter images rely on __del__ and it sucks)
        self.bind(
            "<Destroy>", (lambda e: setattr(self, "channel_image", None)), add=True
        )
        self.bind("<Destroy>", (lambda e: setattr(self, "pm_image", None)), add=True)

        _fix_tag_coloring_bug()
        self.view_selector = ttk.Treeview(self, show="tree", selectmode="browse")
        self.view_selector.tag_configure("new_message", foreground="red")
        self.add(self.view_selector, weight=0)  # don't stretch
        self._contextmenu = tkinter.Menu(tearoff=False)

        self._previous_view: View | None = None
        self.view_selector.bind("<<TreeviewSelect>>", self._current_view_changed)

        if sys.platform == "darwin":
            self.view_selector.bind(
                "<Button-2>", self._view_selector_right_click, add=True
            )
            self.view_selector.bind(
                "<Control-Button-1>", self._view_selector_right_click, add=True
            )
        else:
            self.view_selector.bind(
                "<Button-3>", self._view_selector_right_click, add=True
            )

        self._middle_pane = ttk.Frame(self)
        self.add(self._middle_pane, weight=1)  # always stretch

        entryframe = ttk.Frame(self._middle_pane)
        entryframe.pack(side="bottom", fill="x")
        # TODO: add a tooltip to the button, it's not very obvious
        self._nickbutton = ttk.Button(
            entryframe,
            text=server_config["nick"],
            command=self._show_change_nick_dialog,
        )
        self._nickbutton.pack(side="left")
        self.entry = ttk.Entry(entryframe)
        self.entry.pack(side="left", fill="both", expand=True)
        self.entry.bind("<Return>", self.on_enter_pressed)
        self.entry.bind("<Tab>", self._tab_event_handler)
        self.entry.bind("<Prior>", self._on_page_up)
        self.entry.bind("<Next>", self._on_page_down)

        # {channel_like.name: channel_like}
        self.views_by_id: dict[str, View] = {}
        self.server_view = ServerView(self, self.core.host)
        self.add_view(self.server_view)

    def get_current_view(self) -> View:
        [view_id] = self.view_selector.selection()
        return self.views_by_id[view_id]

    def _show_change_nick_dialog(self) -> None:
        new_nick = ask_new_nick(self.winfo_toplevel(), self.core.nick)
        if new_nick != self.core.nick:
            self.core.change_nick(new_nick)

    def on_enter_pressed(self, junk_event: object = None) -> None:
        from irc_client.commands import handle_command  # import cycle

        handle_command(self.get_current_view(), self.core, self.entry.get())
        self.entry.delete(0, "end")

    def _on_page_up(self, junk_event: object) -> None:
        self.get_current_view().textwidget.yview_scroll(-1, "pages")

    def _on_page_down(self, junk_event: object) -> None:
        self.get_current_view().textwidget.yview_scroll(1, "pages")

    def _tab_event_handler(self, junk_event: object) -> str:
        self.autocomplete()
        return "break"

    # TODO: shift+tab = backwards ?
    def autocomplete(self) -> None:
        view = self.get_current_view()
        if not isinstance(view, ChannelView):
            return

        match = re.fullmatch(r"(.*\s)?([^\s:]+):? ?", self.entry.get())
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
        self.entry.delete(0, "end")
        self.entry.insert(0, new_text)
        self.entry.icursor("end")

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
        new_view.textwidget.pack(
            in_=self._middle_pane, side="top", fill="both", expand=True
        )

        self._previous_view = new_view
        self._mark_seen()

    def add_view(self, view: View) -> None:
        self.views_by_id[view.view_id] = view
        self.view_selector.selection_set(view.view_id)

    def remove_view(self, view: ChannelView | PMView) -> None:
        if self.get_current_view() == view:
            self.view_selector.selection_set(
                self.view_selector.next(view.view_id)
                or self.view_selector.prev(view.view_id)
            )
        self.view_selector.delete(view.view_id)
        view.destroy_widgets()
        del self.views_by_id[view.view_id]

    def find_channel(self, name: str) -> ChannelView | None:
        for view in self.views_by_id.values():
            if isinstance(view, ChannelView) and view.name == name:
                return view
        return None

    def find_pm(self, nick: str) -> PMView | None:
        for view in self.views_by_id.values():
            # TODO: case insensitive
            if isinstance(view, PMView) and view.nick == nick:
                return view
        return None

    def _view_selector_right_click(
        self, event: tkinter.Event[tkinter.ttk.Treeview]
    ) -> None:
        item_id = self.view_selector.identify_row(event.y)
        if not item_id:
            return
        self.view_selector.selection_set(item_id)

        channel = self.get_current_view()
        if not isinstance(channel, ChannelView):
            return

        def on_change(*junk: object) -> None:
            assert isinstance(channel, ChannelView)  # mypy awesomeness
            self._extra_notifications ^= {channel.name}

        var = tkinter.BooleanVar(value=(channel.name in self._extra_notifications))
        var.trace_add("write", on_change)
        self._garbage_collection_is_lol = var

        self._contextmenu.delete(0, "end")
        self._contextmenu.add_checkbutton(
            label="Show notifications for all messages", variable=var
        )
        self._contextmenu.tk_popup(event.x_root, event.y_root)

    def handle_events(self) -> None:
        """Call this once to start processing events from the core."""
        # this is here so that this will be called again, even if
        # something raises an error this time
        next_call_id = self.after(100, self.handle_events)

        while True:
            try:
                event = self.core.event_queue.get(block=False)
            except queue.Empty:
                break

            if isinstance(event, backend.SelfJoined):
                channel_view = self.find_channel(event.channel)
                if channel_view is None:
                    self.add_view(ChannelView(self, event.channel, event.nicklist))
                else:
                    # Can exist already, when has been disconnected from server
                    channel_view.userlist.set_nicks(event.nicklist)

                if event.channel not in self.core.autojoin:
                    self.core.autojoin.append(event.channel)

            elif isinstance(event, backend.SelfParted):
                channel_view = self.find_channel(event.channel)
                assert channel_view is not None
                self.remove_view(channel_view)
                if event.channel in self.core.autojoin:
                    self.core.autojoin.remove(event.channel)

            elif isinstance(event, backend.SelfChangedNick):
                self._nickbutton.config(text=event.new)
                for view in self.views_by_id.values():
                    view.on_self_changed_nick(event.old, event.new)

            elif isinstance(event, backend.SelfQuit):
                (self._on_quit or self.destroy)()
                self.after_cancel(next_call_id)
                return  # don't run self.handle_events again

            elif isinstance(event, backend.UserJoined):
                channel_view = self.find_channel(event.channel)
                assert channel_view is not None
                channel_view.on_join(event.nick)

            elif isinstance(event, backend.UserParted):
                channel_view = self.find_channel(event.channel)
                assert channel_view is not None
                channel_view.on_part(event.nick, event.reason)

            elif isinstance(event, backend.UserQuit):
                for view in self.views_by_id.values():
                    if event.nick in view.get_relevant_nicks():
                        view.on_relevant_user_quit(event.nick, event.reason)

            elif isinstance(event, backend.UserChangedNick):
                for view in self.views_by_id.values():
                    if event.old in view.get_relevant_nicks():
                        view.on_relevant_user_changed_nick(event.old, event.new)

            elif isinstance(event, backend.SentPrivmsg):
                channel_view = self.find_channel(event.recipient)
                if channel_view is None:
                    assert not re.fullmatch(backend.CHANNEL_REGEX, event.recipient)
                    pm_view = self.find_pm(event.recipient)
                    if pm_view is None:
                        # start of a new PM conversation
                        pm_view = PMView(self, event.recipient)
                        self.add_view(pm_view)
                    pm_view.on_privmsg(self.core.nick, event.text)
                else:
                    channel_view.on_privmsg(self.core.nick, event.text)

            elif isinstance(event, backend.ReceivedPrivmsg):
                # sender and recipient are channels or nicks
                if event.recipient == self.core.nick:  # PM
                    pm_view = self.find_pm(event.sender)
                    if pm_view is None:
                        # start of a new PM conversation
                        pm_view = PMView(self, event.sender)
                        self.add_view(pm_view)
                    pm_view.on_privmsg(event.sender, event.text)
                    self._new_message_notify(pm_view, event.text)

                else:
                    channel_view = self.find_channel(event.recipient)
                    assert channel_view is not None

                    pinged = bool(backend.find_nicks(event.text, [self.core.nick]))
                    channel_view.on_privmsg(event.sender, event.text, pinged=pinged)
                    if pinged or (channel_view.name in self._extra_notifications):
                        self._new_message_notify(
                            channel_view, f"<{event.sender}> {event.text}"
                        )

            # TODO: do something to unknown messages!! maybe log in backend?
            elif isinstance(event, (backend.ServerMessage, backend.UnknownMessage)):
                self.server_view.add_message(
                    event.sender or "???", " ".join(event.args)
                )

            elif isinstance(event, backend.ConnectivityMessage):
                for view in self.views_by_id.values():
                    view.on_connectivity_message(event.message, error=event.is_error)

            else:
                # If mypy says 'error: unused "type: ignore" comment', you
                # forgot to check for some class
                print("can't happen")  # type: ignore

    def _window_has_focus(self) -> bool:
        return bool(self.tk.eval("focus"))

    # TODO: /me's and stuff should also call this when they are supported
    def _new_message_notify(
        self, view: ChannelView | PMView, message_with_sender: str
    ) -> None:
        if isinstance(view, ChannelView):
            channel_name_or_nick = view.name
        else:
            channel_name_or_nick = view.nick

        if not self._window_has_focus():
            _show_popup(channel_name_or_nick, message_with_sender)

        if view != self.get_current_view():
            # TODO: don't clear other tags, if there will be any
            self.view_selector.item(view.view_id, tags="new_message")
            self.event_generate("<<NotSeenCountChanged>>")

    def _mark_seen(self) -> None:
        view = self.get_current_view()
        if isinstance(view, (ChannelView, PMView)):
            # TODO: don't erase all tags if there will be other tags later
            self.view_selector.item(view.view_id, tags="")
            self.event_generate("<<NotSeenCountChanged>>")

    def not_seen_count(self) -> int:
        """Returns the number of channel-likes that are shown in red.

        A <<NotSeenCountChanged>> event is generated when the value may
        have changed.
        """
        result = 0
        for view in self.views_by_id.values():
            if isinstance(view, (ServerView, PMView)):
                tags = self.view_selector.item(view.view_id, "tags")
                if "new_message" in tags:
                    result += 1
        return result

    def get_current_config(self) -> config.ServerConfig:
        return {
            "host": self.core.host,
            "port": self.core.port,
            "ssl": self.core.ssl,
            "nick": self.core.nick,
            "username": self.core.username,
            "realname": self.core.realname,
            "joined_channels": self.core.autojoin.copy(),
            "extra_notifications": list(self._extra_notifications),
        }
