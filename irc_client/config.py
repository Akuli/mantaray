from __future__ import annotations
import functools
import re
import tkinter
from tkinter import ttk
import sys
from typing import Any, TYPE_CHECKING

if sys.version_info >= (3, 8):
    from typing import TypedDict
else:
    if TYPE_CHECKING:
        from typing_extensions import TypedDict
    else:
        TypedDict = object


class ServerConfig(TypedDict):
    host: str
    port: int
    nick: str  # TODO: multiple choices, in case already in use
    username: str
    realname: str
    join_channels: list[str]


class Config:
    servers: list[ServerConfig]  # TODO: support multiple servers in gui


# TODO: get rid of this?
class _EntryWithVar(ttk.Entry):
    def __init__(self, master: tkinter.Misc, **kwargs: Any):
        var = tkinter.StringVar()
        super().__init__(master, textvariable=var, **kwargs)
        self.var = var


class _ServerConfigurer(ttk.Frame):
    def __init__(self, master: tkinter.Misc, initial_config: ServerConfig):
        super().__init__(master)

        self.result: ServerConfig | None = None

        self._rownumber = 0
        self.grid_columnconfigure(0, minsize=60)
        self.grid_columnconfigure(1, weight=1)

        self._server_entry = self._create_entry()
        self._add_row("Server:", self._server_entry)

        self._channel_entry = self._create_entry()
        self._add_row("Channel:", self._channel_entry)

        self._nick_entry = self._create_entry()
        self._nick_entry.var.trace("w", self._on_nick_changed)
        self._add_row("Nickname:", self._nick_entry)

        button = ttk.Button(self, text="More options...")
        button.config(command=functools.partial(self._show_more, button))
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
            self._bottomframe, text="Connect!", command=self.connect_clicked
        )
        self._connectbutton.pack(side="right")

        # now everything's ready for _validate()
        # all of these call validate()
        self._server_entry.var.set(initial_config["host"])
        self._port_entry.var.set(str(initial_config["port"]))
        self._nick_entry.var.set(initial_config["nick"])
        self._username_entry.var.set(initial_config["username"])
        self._realname_entry.var.set(initial_config["realname"])
        self._channel_entry.var.set(" ".join(initial_config["join_channels"]))

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

    def _create_entry(self, **kwargs: Any) -> _EntryWithVar:
        entry = _EntryWithVar(self, **kwargs)
        entry.var.trace("w", self._validate)
        return entry

    def _setup_entry_bindings(self, entry: ttk.Entry) -> None:
        entry.bind("<Return>", self.connect_clicked, add=True)
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
        self.winfo_toplevel().destroy()

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

        from .backend import NICK_REGEX, CHANNEL_REGEX

        if not re.fullmatch(NICK_REGEX, self._nick_entry.get()):
            self._statuslabel["text"] = (
                "'%s' is not a valid nickname." % self._nick_entry.get()
            )
            return False

        # if the channel entry is empty, no channels are joined
        channels = self._channel_entry.get().split()
        for channel in channels:
            if not re.fullmatch(CHANNEL_REGEX, channel):
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

    def connect_clicked(self, junk_event: object = None) -> None:
        assert self._validate()
        self.result = {
            "host": self._server_entry.get(),
            "port": int(self._port_entry.get()),
            "nick": self._nick_entry.get(),
            "username": self._username_entry.get(),
            "realname": self._realname_entry.get(),
            "join_channels": self._channel_entry.get().split(),
        }
        self.winfo_toplevel().destroy()


# returns None if user cancel
def show_server_config_dialog(
    transient_to: tkinter.Tk | None, initial_config: ServerConfig
) -> ServerConfig | None:

    dialog = tkinter.Toplevel()
    content = _ServerConfigurer(dialog, initial_config)
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
