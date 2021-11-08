from __future__ import annotations
import functools
import getpass  # for getting the user name
import logging
import re
import threading
import tkinter
from tkinter import ttk
import traceback
from typing import Callable, Any

from . import backend

log = logging.getLogger(__name__)


# TODO: get rid of this?
class EntryWithVar(ttk.Entry):
    def __init__(self, master: tkinter.Misc, **kwargs: Any):
        var = tkinter.StringVar()
        super().__init__(master, textvariable=var, **kwargs)
        self.var = var


# TODO: this is ok for connecting the first time, but the defaults should go
#       to a config file or something
class ConnectDialogContent(ttk.Frame):
    def __init__(
        self,
        master: tkinter.Misc,
        on_cancel_or_after_connect: Callable[[], None],
        **kwargs: Any
    ):
        super().__init__(master, **kwargs)
        self._on_cancel_or_after_connect = on_cancel_or_after_connect

        self.result: backend.IrcCore | None = None

        self._rownumber = 0
        self.grid_columnconfigure(0, minsize=60)
        self.grid_columnconfigure(1, weight=1)

        self._server_entry = self._create_entry()
        self._add_row("Server:", self._server_entry)

        self._channel_entry = self._create_entry()
        self._add_row("Channel:", self._channel_entry)
        self._channel_entry.var.set("##learnpython")

        self._nick_entry = self._create_entry()
        self._nick_entry.var.trace("w", self._on_nick_changed)
        self._add_row("Nickname:", self._nick_entry)

        button = ttk.Button(self, text="More options...")
        button["command"] = functools.partial(self._show_more, button)
        button.grid(
            row=self._rownumber, column=0, columnspan=4, sticky="w", padx=5, pady=5
        )
        # leave self._rownumber untouched

        # _show_more() grids these
        self._username_entry = self._create_entry()
        self._realname_entry = self._create_entry()
        self._port_entry = self._create_entry(width=8)

        # big row makes sure that this is always below everything
        self._statuslabel = ttk.Label(self)
        self._statuslabel.grid(row=30, column=0, columnspan=4, pady=5, sticky="swe")
        self._statuslabel.bind(
            "<Configure>",
            lambda event: self._statuslabel.config(wraplength=event.width),
        )
        self.grid_rowconfigure(30, weight=1)

        self._bottomframe = ttk.Frame(self)
        self._bottomframe.grid(
            row=31, column=0, columnspan=4, padx=5, pady=5, sticky="we"
        )

        ttk.Button(self._bottomframe, text="Cancel", command=self.cancel).pack(
            side="right"
        )
        self._connectbutton = ttk.Button(
            self._bottomframe, text="Connect!", command=self.connect
        )
        self._connectbutton.pack(side="right")

        # now everything's ready for _validate()
        # all of these call validate()
        self._server_entry.var.set("irc.libera.chat")
        self._nick_entry.var.set(getpass.getuser())
        self._port_entry.var.set("6697")
        self._on_nick_changed()

    # TODO: 2nd alternative for nicknames
    # rest of the code should also handle nickname errors better
    # https://tools.ietf.org/html/rfc1459#section-4.1.2
    def _show_more(self, show_more_button: ttk.Button) -> None:
        show_more_button.destroy()

        self._server_entry.grid_configure(columnspan=1)
        ttk.Label(self, text="Port:").grid(row=0, column=2)
        self._port_entry.grid(row=0, column=3)
        self._setup_entry_bindings(self._port_entry)

        self._add_row("Username:", self._username_entry)
        self._add_row("Real* name:", self._realname_entry)

        infolabel = ttk.Label(
            self,
            text=(
                "* This doesn't need to be your real name.\n"
                "   You can set this to anything you want."
            ),
        )
        infolabel.grid(
            row=self._rownumber, column=0, columnspan=4, sticky="w", padx=5, pady=5
        )
        self._rownumber += 1

        self.event_generate("<<MoreOptions>>")

    def _create_entry(self, **kwargs: Any) -> EntryWithVar:
        entry = EntryWithVar(self, **kwargs)
        entry.var.trace("w", self._validate)
        return entry

    def _setup_entry_bindings(self, entry: ttk.Entry) -> None:
        entry.bind("<Return>", self.connect, add=True)
        entry.bind("<Escape>", self.cancel, add=True)

    def _add_row(self, label: str, widget: ttk.Entry) -> None:
        ttk.Label(self, text=label).grid(row=self._rownumber, column=0, sticky="w")
        widget.grid(row=self._rownumber, column=1, columnspan=3, sticky="we")
        self._setup_entry_bindings(widget)
        self._rownumber += 1

    def _on_nick_changed(self, *junk: object) -> None:
        # these call self._validate()
        self._username_entry.var.set(self._nick_entry.get())
        self._realname_entry.var.set(self._nick_entry.get())

    def cancel(self, junk_event: object = None) -> None:
        self._on_cancel_or_after_connect()

    def _validate(self, *junk: object) -> bool:
        # this will be re-enabled if everything's ok
        self._connectbutton["state"] = "disabled"

        if not self._server_entry.get():
            self._statuslabel["text"] = "Please specify a server."
            return False
        if not self._nick_entry.get():
            self._statuslabel["text"] = "Please specify a nickname."
            return False
        if not self._username_entry.get():
            self._statuslabel["text"] = "Please specify a username."
            return False
        # TODO: can realname be empty?

        if not re.search("^" + backend.NICK_REGEX + "$", self._nick_entry.get()):
            self._statuslabel["text"] = (
                "'%s' is not a valid nickname." % self._nick_entry.get()
            )
            return False

        # if the channel entry is empty, no channels are joined
        channels = self._channel_entry.get().split()
        for channel in channels:
            if not re.fullmatch(backend.CHANNEL_REGEX, channel):
                self._statuslabel["text"] = (
                    "'%s' is not a valid channel name." % channel
                )

                # see comments of backend.CHANNEL_REGEX
                if not channel.startswith(("&", "#", "+", "!")):
                    # the user probably doesn't know what (s)he's doing
                    self._statuslabel[
                        "text"
                    ] += " Usually channel names start with a # character."
                return False

        try:
            port = int(self._port_entry.get())
            if port <= 0:
                raise ValueError
        except ValueError:
            self._statuslabel["text"] = "The port must be a positive integer."
            return False

        self._statuslabel["text"] = ""
        self._connectbutton["state"] = "normal"
        return True

    def _connect_with_thread(
        self, core: backend.IrcCore, done_callback: Callable[[str | None], object]
    ) -> None:
        error: str | None = None

        def this_runs_in_thread() -> None:
            nonlocal error
            try:
                core.connect()
            except Exception:
                error = traceback.format_exc()

        thread = threading.Thread(target=this_runs_in_thread)
        thread.start()

        def this_runs_in_tk_mainloop() -> None:
            if thread.is_alive():
                done_callback(error)
            else:
                self.after(100, this_runs_in_tk_mainloop)

        this_runs_in_tk_mainloop()

    def connect(self, junk_event: object = None) -> None:
        """Create an IrcCore.

        On success, this sets self.result to the connected core and
        calls on_cancel_or_after_connect(), and on error this shows an
        error message instead.
        """
        assert self._validate()
        disabled = self.winfo_children() + self._bottomframe.winfo_children()
        disabled.remove(self._bottomframe)
        disabled.remove(self._statuslabel)
        for widget in disabled:
            widget["state"] = "disabled"

        progressbar = ttk.Progressbar(self._bottomframe, mode="indeterminate")
        progressbar.pack(side="left", fill="both", expand=True)
        progressbar.start()
        self._statuslabel["text"] = "Connecting..."

        # creating an IrcCore creates a socket, but that shouldn't block
        # toooo much
        core = backend.IrcCore(
            self._server_entry.get(),
            int(self._port_entry.get()),
            self._nick_entry.get(),
            self._username_entry.get(),
            self._realname_entry.get(),
            autojoin=self._channel_entry.get().split(),
        )

        def on_connected(error: str | None) -> None:
            # this stuff must be ran from tk's event loop
            for widget in disabled:
                widget["state"] = "normal"
            progressbar.destroy()

            if error is None:
                self.result = core
                self._on_cancel_or_after_connect()
            else:
                # error is a traceback string
                log.error("connecting to %s:%d failed\n%s", core.host, core.port, error)

                last_line = error.splitlines()[-1]
                self._statuslabel["text"] = "Connecting to %s failed!\n%s" % (
                    core.host,
                    last_line,
                )

        self._connect_with_thread(core, on_connected)


def run(transient_to: tkinter.Tk | None = None) -> backend.IrcCore | None:
    """Returns a connected IrcCore, or None if the user cancelled."""
    dialog = tkinter.Toplevel()
    content = ConnectDialogContent(dialog, dialog.destroy)
    content.pack(fill="both", expand=True)

    dialog.minsize(350, 200)
    content.bind(
        "<<MoreOptions>>", lambda junk_event: dialog.minsize(350, 250), add=True
    )

    dialog.title("Connect to IRC")
    if transient_to is not None:
        dialog.transient(transient_to)
    dialog.wait_window()
    return content.result
