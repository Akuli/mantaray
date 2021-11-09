# strongly inspired by xchat :)
# hexchat is a fork of xchat, so hexchat developers didn't invent this
#
# TODO: seems like channel names don't need to start with #
#       https://tools.ietf.org/html/rfc2812#section-2.3.1
from __future__ import annotations
import collections.abc
import hashlib
import os
import queue
import re
import subprocess
import sys
import time
import tkinter
import traceback
from tkinter import ttk
from typing import Callable, TYPE_CHECKING, overload, Iterable, Any

from . import backend, colors, commands


def _show_popup(title: str, text: str) -> None:
    try:
        if sys.platform == "win32":
            # FIXME
            print("Sorry, no popups on windows yet :(")
        elif sys.platform == "darwin":
            # https://stackoverflow.com/a/41318195
            # TODO: can this be indented too?
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


if TYPE_CHECKING:
    _base = collections.abc.MutableSequence[str]
else:
    _base = collections.abc.MutableSequence


# because tkinter sucks at this
class TreeviewWrapper(_base):
    """An easier way to use ttk.Treeview for non-nested data.

    This behaves like a list of strings, and the treeview is updated
    automatically.
    """

    def __init__(self, treeview: ttk.Treeview):
        self.widget = treeview

    def __len__(self) -> int:
        # tkinter uses a children attribute for another thing, so the method
        # that calls 'pathname children item' from ttk_treeview(3tk) is
        # named get_children()
        return len(self.widget.get_children(""))

    @overload
    def __getitem__(self, index: int) -> str:
        ...

    @overload
    def __getitem__(self, index: slice) -> list[str]:
        ...

    def __getitem__(self, index: int | slice) -> str | list[str]:
        return list(self.widget.get_children(""))[index]

    def __delitem__(self, index: int | slice) -> None:
        assert not isinstance(index, slice), "deleting slices not supported"
        self.widget.delete(self.widget.get_children("")[index])

    def insert(self, index: int, value: str) -> None:
        self.widget.insert("", index, value, text=value)

    def __setitem__(self, index: int | slice, new_value: str | Iterable[str]) -> None:
        assert not isinstance(index, slice), "slicing not supported :("
        assert isinstance(new_value, str)

        # preserve as much info of the old value as possible
        item_options = self.widget.item(self.widget.get_children("")[index])
        was_selected = self[index] in self.widget.selection()

        del self[index]
        self.insert(index, new_value)

        # don't restore the old text, it was probably the old value
        # self.insert() has set a new text
        del item_options["text"]
        self.widget.item(new_value, **item_options)
        if was_selected:
            self.widget.selection_set(new_value)

    def select_something_else(
        self, than_this_item: str
    ) -> None:  # https://xkcd.com/1960/
        if than_this_item in self.widget.selection():
            if than_this_item == self[-1]:
                self.widget.selection_set(self[-2])
            else:
                self.widget.selection_set(self[self.index(than_this_item) + 1])


# channel-like views are listed in the gui at left
# this is the name of the special server channel-like
# this random MD5 is veeery unlikely to collide with anyone's nick
# nicks also have a length limit that is way smaller than this
_SERVER_VIEW_ID = hashlib.md5(os.urandom(3)).hexdigest()


# represents the IRC server, a channel or a PM conversation
class ChannelLikeView:

    # users is a list of nicks or None if this is not a channel
    # name is a nick or channel name, nick name for PMS or _SERVER_VIEW_ID
    def __init__(self, ircwidget: IrcWidget, name: str, users: list[str] | None = None):
        # if someone changes nick, IrcWidget takes care of updating .name
        self.name = name

        # width and height are minimums
        # IrcWidget packs this and lets this stretch
        self.textwidget = colors.ColoredText(
            ircwidget, width=1, height=1, state="disabled"
        )

        if users is None:
            self.userlist = None
        else:
            # bigpanedw adds the treeview to itself when needed
            treeview = ttk.Treeview(ircwidget, show="tree", selectmode="extended")
            self.userlist = TreeviewWrapper(treeview)
            self.userlist.extend(sorted(users, key=str.casefold))

    # 'wat.is_channel()' is way more readable than 'wat.userlist is not None'
    def is_channel(self) -> bool:
        return self.userlist is not None

    def destroy_widgets(self) -> None:
        """This is called by IrcWidget.remove_channel_like()."""
        self.textwidget.destroy()
        if self.userlist is not None:
            self.userlist.widget.destroy()

    def add_message(
        self,
        sender: str,
        message: str,
        automagic_nick_coloring: bool = False,
        *,
        pinged: bool = False,
    ) -> None:
        """Add a message to self.textwidget."""
        # scroll down all the way if the user hasn't scrolled up manually
        do_the_scroll = self.textwidget.yview()[1] == 1.0

        # nicks are limited to 16 characters at least on freenode
        # len(sender) > 16 is not a problem:
        #    >>> ' ' * (-3)
        #    ''
        padding = " " * (16 - len(sender))

        self.textwidget["state"] = "normal"
        self.textwidget.insert("end", "[%s] %s" % (time.strftime("%H:%M"), padding))
        self.textwidget.colored_insert("end", colors.color_nick(sender), pinged=False)
        self.textwidget.insert("end", " | ")
        if self.userlist is None or not automagic_nick_coloring:
            self.textwidget.colored_insert("end", message, pinged)
        else:
            self.textwidget.nicky_insert("end", message, list(self.userlist), pinged)
        self.textwidget.insert("end", "\n")
        self.textwidget["state"] = "disabled"

        if do_the_scroll:
            self.textwidget.see("end")

    def on_privmsg(self, sender: str, message: str, pinged: bool = False) -> None:
        self.add_message(
            sender,
            message,
            automagic_nick_coloring=True,
            pinged=pinged,
        )

    def on_join(self, nick: str) -> None:
        """Called when another user joins this channel."""
        assert self.is_channel()
        assert self.userlist is not None

        # TODO: a better algorithm?
        #       timsort is good, but maybe sorting every time is not?
        nicks = list(self.userlist)
        nicks.append(nick)
        nicks.sort(key=str.casefold)
        self.userlist.insert(nicks.index(nick), nick)

        self.add_message("*", "%s joined %s." % (colors.color_nick(nick), self.name))

    def on_part(self, nick: str, reason: str) -> None:
        """Called when another user leaves this channel."""
        assert self.is_channel()
        assert self.userlist is not None
        self.userlist.remove(nick)

        msg = "%s left %s." % (colors.color_nick(nick), self.name)
        if reason is not None:
            msg = "%s (%s)" % (msg, reason)
        self.add_message("*", msg)

    def on_quit(self, nick: str, reason: str) -> None:
        """Called when a user that was on this channel quits the whole IRC.

        This is also called if the channel-like is not a channel, but it
        represents a PM conversation with the quitting user.
        """
        if self.is_channel():
            assert self.userlist is not None
            self.userlist.remove(nick)
        else:  # a PM conversation
            if self.name != nick:
                # this conversation is between the user and someone else
                return

        msg = "%s quit." % colors.color_nick(nick)
        if reason is not None:
            msg = "%s (%s)" % (msg, reason)
        self.add_message("*", msg)

    def on_self_changed_nick(self, old: str, new: str) -> None:
        """Called after the user of this thing changes nick successfully."""
        # if this is a channel, update the list of nicks
        if self.userlist is not None:
            self.userlist[self.userlist.index(old)] = new

        # notify about the nick change everywhere, no ifs in front of this
        self.add_message("*", "You are now known as %s." % colors.color_nick(new))

    def on_user_changed_nick(self, old: str, new: str) -> None:
        """Called after anyone has changed nick.

        This must be called AFTER updating self.name.
        """
        if self.name == _SERVER_VIEW_ID:
            # no need to do anything on the server channel-like
            return

        if self.is_channel():
            assert self.userlist is not None
            if old not in self.userlist:
                return
            self.userlist[self.userlist.index(old)] = new
        else:
            # PM chat, only display the nick change if chatting with that nick
            if self.name != new:
                return

        self.add_message(
            "*",
            "%s is now known as %s." % (colors.color_nick(old), colors.color_nick(new)),
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
        irc_core: backend.IrcCore,
        on_quit: Callable[[], object],
    ):
        super().__init__(master, orient="horizontal")
        self.core = irc_core
        self._command_handler = commands.CommandHandler(irc_core)
        self._on_quit = on_quit

        images_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "images")
        self._channel_image = tkinter.PhotoImage(
            file=os.path.join(images_dir, "hashtagbubble-20x20.png")
        )
        self._pm_image = tkinter.PhotoImage(
            file=os.path.join(images_dir, "face-20x20.png")
        )

        _fix_tag_coloring_bug()
        treeview = ttk.Treeview(self, show="tree", selectmode="browse")
        treeview.tag_configure("new_message", foreground="red")
        treeview.bind("<<TreeviewSelect>>", self._on_selection)
        self.add(treeview, weight=0)  # don't stretch
        self._channel_selector = TreeviewWrapper(treeview)

        self._middle_pane = ttk.Frame(self)
        self.add(self._middle_pane, weight=1)  # always stretch

        entryframe = ttk.Frame(self._middle_pane)
        entryframe.pack(side="bottom", fill="x")
        # TODO: add a tooltip to the button, it's not very obvious
        self._nickbutton = ttk.Button(
            entryframe, text=irc_core.nick, command=self._show_change_nick_dialog
        )
        self._nickbutton.pack(side="left")
        self._entry = ttk.Entry(entryframe)
        self._entry.pack(side="left", fill="both", expand=True)
        self._entry.bind("<Return>", self._on_enter_pressed)
        self._entry.bind("<Tab>", self._autocomplete)

        self._channel_likes: dict[
            str, ChannelLikeView
        ] = {}  # {channel_like.name: channel_like}
        self._current_channel_like: ChannelLikeView | None = None
        self.add_channel_like(ChannelLikeView(self, _SERVER_VIEW_ID))

    def focus_the_entry(self) -> None:
        self._entry.focus()

    def _show_change_nick_dialog(self) -> None:
        new_nick = ask_new_nick(self.winfo_toplevel(), self.core.nick)
        if new_nick != self.core.nick:
            self.core.change_nick(new_nick)

    def _on_enter_pressed(self, event: object) -> None:
        assert self._current_channel_like is not None
        response = self._command_handler.handle_command(
            self._current_channel_like.name, self._entry.get()
        )
        self._entry.delete(0, "end")
        if response is not None:
            self._current_channel_like.add_message("*", response)

    def _autocomplete(self, event: object) -> None:
        channel_like = self._current_channel_like
        assert channel_like is not None
        if channel_like.userlist is None:
            return

        match = re.fullmatch(r"(.*\s)?([^\s:]+):? ?", self._entry.get())
        if match is None:
            return
        preceding_text, last_word = match.groups()  # preceding_text can be None

        if last_word in channel_like.userlist:
            index = channel_like.userlist.index(last_word)
            index = (index + 1) % len(
                channel_like.userlist
            )  # TODO: shift+tab = backwards ?
            completion = channel_like.userlist[index]
        else:
            try:
                completion = next(
                    username
                    for username in channel_like.userlist
                    if username.lower().startswith(last_word.lower())
                )
            except StopIteration:
                return

        if preceding_text:
            new_text = preceding_text + completion + " "
        else:
            new_text = completion + ": "
        self._entry.delete(0, "end")
        self._entry.insert(0, new_text)
        self._entry.icursor("end")

    def _on_selection(self, event: object) -> None:
        (name,) = self._channel_selector.widget.selection()
        new_channel_like = self._channel_likes[name]
        if self._current_channel_like is not None:
            # not running for the first time
            if self._current_channel_like is new_channel_like:
                return
            if self._current_channel_like.userlist is not None:
                self.remove(self._current_channel_like.userlist.widget)
            self._current_channel_like.textwidget.pack_forget()

        new_channel_like.textwidget.pack(
            in_=self._middle_pane, side="top", fill="both", expand=True
        )
        if new_channel_like.userlist is not None:
            self.add(new_channel_like.userlist.widget, weight=0)

        self._current_channel_like = new_channel_like
        self.mark_seen()

    def add_channel_like(self, channel_like: ChannelLikeView) -> None:
        assert channel_like.name not in self._channel_likes
        self._channel_likes[channel_like.name] = channel_like

        self._channel_selector.append(channel_like.name)
        self._channel_selector.widget.selection_set(channel_like.name)

        if channel_like.name == _SERVER_VIEW_ID:
            assert len(self._channel_likes) == 1
            self._channel_selector.widget.item(channel_like.name, text=self.core.host)
        elif channel_like.is_channel():
            self._channel_selector.widget.item(
                channel_like.name, image=self._channel_image
            )
        else:
            self._channel_selector.widget.item(channel_like.name, image=self._pm_image)

    def remove_channel_like(self, channel_like: ChannelLikeView) -> None:
        assert (
            channel_like.name != _SERVER_VIEW_ID
        ), "cannot remove the server channel-like"

        if channel_like is self._current_channel_like:
            self._current_channel_like = None
        self._channel_selector.select_something_else(channel_like.name)
        self._channel_selector.remove(channel_like.name)
        channel_like.destroy_widgets()

    # this must be called when someone that the user PM's with changes nick
    # channels and the special server channel-like can't be renamed
    def rename_channel_like(self, old_name: str, new_name: str) -> None:
        assert (
            old_name != _SERVER_VIEW_ID and new_name != _SERVER_VIEW_ID
        ), "cannot rename the server channel-like"

        if new_name in self._channel_likes:
            # unlikely to ever happen, but possible with a funny
            # combination of nick changes... lol
            self.remove_channel_like(self._channel_likes[new_name])

        self._channel_likes[new_name] = self._channel_likes.pop(old_name)
        self._channel_likes[new_name].name = new_name
        index = self._channel_selector.index(old_name)
        self._channel_selector[index] = new_name

    def handle_events(self) -> None:
        """Call this once to start processing events from the core."""
        # this is here so that this will be called again, even if
        # something raises an error this time
        next_call_id = self.after(100, self.handle_events)

        while True:
            try:
                event, *event_args = self.core.event_queue.get(block=False)
            except queue.Empty:
                break

            if event == backend.IrcEvent.self_joined:
                channel, nicklist = event_args
                self.add_channel_like(ChannelLikeView(self, channel, nicklist))

            elif event == backend.IrcEvent.self_changed_nick:
                old, new = event_args
                self._nickbutton["text"] = new
                for channel_like in self._channel_likes.values():
                    channel_like.on_self_changed_nick(old, new)

            elif event == backend.IrcEvent.self_parted:
                [channel] = event_args
                self.remove_channel_like(self._channel_likes[channel])

            elif event == backend.IrcEvent.self_quit:
                print("got a self_quit event from core")
                self._on_quit()
                self.after_cancel(next_call_id)
                return  # don't run self.handle_events again

            elif event == backend.IrcEvent.user_joined:
                nick, channel = event_args
                self._channel_likes[channel].on_join(nick)

            elif event == backend.IrcEvent.user_changed_nick:
                old, new = event_args
                if old in self._channel_likes:  # a PM conversation
                    self.rename_channel_like(old, new)

                for channel_like in self._channel_likes.values():
                    channel_like.on_user_changed_nick(old, new)

            elif event == backend.IrcEvent.user_parted:
                nick, channel, reason = event_args
                self._channel_likes[channel].on_part(nick, reason)

            elif event == backend.IrcEvent.user_quit:
                nick, reason = event_args

                for channel_like in self._channel_likes.values():
                    if channel_like.name == _SERVER_VIEW_ID:
                        continue

                    # show a quit message if the user was on this channel
                    # or if this is a PM conversation with that user
                    if nick == channel_like.name or (
                        channel_like.userlist is not None
                        and nick in channel_like.userlist
                    ):
                        channel_like.on_quit(nick, reason)

            elif event == backend.IrcEvent.sent_privmsg:
                recipient, msg = event_args
                if recipient not in self._channel_likes:
                    # start of a new PM conversation with a nick
                    assert not re.fullmatch(backend.CHANNEL_REGEX, recipient)
                    self.add_channel_like(ChannelLikeView(self, recipient))

                self._channel_likes[recipient].on_privmsg(self.core.nick, msg)

            elif event == backend.IrcEvent.received_privmsg:
                # sender and recipient are channels or nicks
                sender, recipient, msg = event_args

                if recipient == self.core.nick:  # PM
                    pinged = True
                    if sender not in self._channel_likes:
                        # create a new channel-like for the conversation
                        self.add_channel_like(ChannelLikeView(self, sender))
                    channel_like_name = sender
                    msg_with_sender = msg

                else:  # the message has been sent to an entire channel
                    assert re.fullmatch(backend.CHANNEL_REGEX, recipient)
                    channel_like_name = recipient

                    mentioned = [
                        nick.lower() for nick in re.findall(backend.NICK_REGEX, msg)
                    ]
                    pinged = self.core.nick.lower() in mentioned
                    msg_with_sender = f"<{sender}> {msg}"

                self._channel_likes[channel_like_name].on_privmsg(
                    sender, msg, pinged=pinged
                )
                if pinged:
                    self._new_message_notify(channel_like_name, msg_with_sender)

            # TODO: do something to unknown messages!! maybe log in backend?
            elif event in {
                backend.IrcEvent.server_message,
                backend.IrcEvent.unknown_message,
            }:
                server, command, args = event_args
                if server is None:
                    # TODO: when does this happen?
                    server = "???"

                # not strictly a privmsg, but handled the same way
                self._channel_likes[_SERVER_VIEW_ID].on_privmsg(server, " ".join(args))

            else:
                raise ValueError("unknown event type " + repr(event))

    # TODO: /me's and stuff should also call this when they are supported
    def _new_message_notify(
        self, channel_like_name: str, message_with_sender: str
    ) -> None:
        assert channel_like_name != _SERVER_VIEW_ID

        if not self.tk.eval("focus"):  # window not focused
            _show_popup(channel_like_name, message_with_sender)

        assert self._current_channel_like is not None
        if channel_like_name != self._current_channel_like.name:
            self._channel_selector.widget.item(channel_like_name, tags="new_message")
            self.event_generate("<<NotSeenCountChanged>>")

    def mark_seen(self) -> None:
        """Make the currently selected channel-like not red in the list.

        This should be called when the user has a chance to read new
        messages in the channel-like.
        """
        assert self._current_channel_like is not None
        if self._current_channel_like.name != _SERVER_VIEW_ID:
            # TODO: don't erase all tags if there will be other tags later
            self._channel_selector.widget.item(self._current_channel_like.name, tags="")
            self.event_generate("<<NotSeenCountChanged>>")

    def not_seen_count(self) -> int:
        """Returns the number of channel-likes that are shown in red.

        A <<NotSeenCountChanged>> event is generated when the value may
        have changed.
        """
        result = 0
        for name in self._channel_likes.keys() - {_SERVER_VIEW_ID}:
            tags = self._channel_selector.widget.item(name, "tags")
            if "new_message" in tags:
                result += 1
        return result

    def part_all_channels_and_quit(self) -> None:
        """Call this to get out of IRC."""
        # the channel is not parted right away, self.core just puts a thing to
        # a queue and does the actual parting later
        # that's why there's no need to copy the items
        for name, channel_like in self._channel_likes.items():
            if channel_like.is_channel():
                # TODO: add a reason here?
                self.core.part_channel(name)
        print("calling self.core.quit()")
        self.core.quit()
