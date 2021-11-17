from __future__ import annotations
import json
import re
import tkinter
import sys
from pathlib import Path
from tkinter import ttk
from getpass import getuser
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
    ssl: bool
    nick: str  # TODO: multiple choices, in case already in use
    username: str
    realname: str
    password: str | None
    joined_channels: list[str]
    extra_notifications: list[str]  # channels to notify for all messages


class Config(TypedDict):
    servers: list[ServerConfig]  # TODO: support multiple servers in gui


def load_from_file(config_dir: Path) -> Config | None:
    try:
        with (config_dir / "config.json").open("r", encoding="utf-8") as file:
            result = json.load(file)
            # Backwards compatibility with older config.json files
            for server in result["servers"]:
                server.setdefault("ssl", True)
                server.setdefault("extra_notifications", [])
                server.setdefault("password", None)
            return result
    except FileNotFoundError:
        return None


def save_to_file(config_dir: Path, config: Config) -> None:
    config_dir.mkdir(parents=True, exist_ok=True)
    with (config_dir / "config.json").open("w", encoding="utf-8") as file:
        json.dump(config, file, indent=2)
        file.write("\n")

    # config.json contains passwords (hexchat stores them in plain text too)
    # TODO: how do permissions work on windows?
    if sys.platform != "win32":
        (config_dir / "config.json").chmod(0o600)


class _EntryWithVar(ttk.Entry):
    def __init__(self, master: tkinter.Misc, **kwargs: Any):
        var = tkinter.StringVar()
        super().__init__(master, textvariable=var, **kwargs)
        self.var = var


class _ConnectDialogContent(ttk.Frame):
    def __init__(
        self,
        master: tkinter.Misc,
    ):
        super().__init__(master)
        self.result: ServerConfig | None = None

        self._rownumber = 0
        self.grid_columnconfigure(0, minsize=60)
        self.grid_columnconfigure(1, weight=1)

        self._server_entry = self._create_entry()
        self._add_row("Server:", self._server_entry)

        ttk.Label(self, text="Port:").grid(row=self._rownumber, column=0, sticky="w")
        self._port_entry = self._create_entry(width=8)
        self._port_entry.grid(row=self._rownumber, column=1, sticky="we")
        self._ssl_var = tkinter.BooleanVar(value=True)
        self._ssl_var.trace("w", self._guess_port_based_on_ssl)
        self._ssl_checkbox = ttk.Checkbutton(
         self, text="Use SSL", variable=self._ssl_var
        )
        self._ssl_checkbox.grid(row=self._rownumber, column=2)
        self._rownumber += 1

        self._channel_entry = self._create_entry()
        self._add_row("Channels: (space-separated)", self._channel_entry)

        self._nick_entry = self._create_entry()
        self._add_row("Nickname:", self._nick_entry)

        self._password_entry = self._create_entry()
        self._add_row("Password (leave empty if none):", self._password_entry)

        # big row makes sure that this is always below everything
        self._statuslabel = ttk.Label(self)
        self._statuslabel.grid(row=30, column=0, columnspan=3, pady=5, sticky="swe")
        self._statuslabel.bind(
            "<Configure>",
            lambda event: self._statuslabel.config(wraplength=event.width),
        )
        self.grid_rowconfigure(30, weight=1)

        self._bottomframe = ttk.Frame(self)
        self._bottomframe.grid(
            row=31, column=0, columnspan=3, padx=5, pady=5, sticky="we"
        )

        ttk.Button(self._bottomframe, text="Cancel", command=self.cancel).pack(
            side="right"
        )
        self._connectbutton = ttk.Button(
            self._bottomframe, text="Connect!", command=self.connect_clicked
        )
        self._connectbutton.pack(side="right")

        # stupid defaults
        self._server_entry.var.set("irc.libera.chat")
        self._port_entry.var.set("6697")
        self._nick_entry.var.set(getuser())
        self._channel_entry.var.set("##learnpython")

    def _create_entry(self, **kwargs: Any) -> _EntryWithVar:
        entry = _EntryWithVar(self, **kwargs)
        entry.var.trace("w", self._validate)
        return entry

    def _setup_entry_bindings(self, entry: ttk.Entry) -> None:
        entry.bind("<Return>", self.connect_clicked, add=True)
        entry.bind("<Escape>", self.cancel, add=True)

    def _add_row(self, label: str, widget: ttk.Entry) -> None:
        ttk.Label(self, text=label).grid(row=self._rownumber, column=0, sticky="w")
        widget.grid(row=self._rownumber, column=1, columnspan=2, sticky="we")
        self._setup_entry_bindings(widget)
        self._rownumber += 1

    def _guess_port_based_on_ssl(self, *junk: object) -> None:
        self._port_entry.delete(0, "end")
        self._port_entry.insert(0, "6697" if self._ssl_var.get() else "6667")

    def cancel(self, junk_event: object = None) -> None:
        self.winfo_toplevel().destroy()

    def _validate(self, *junk: object) -> bool:
        # this will be re-enabled if everything's ok
        self._connectbutton.config(state="disabled")

        if not self._server_entry.get():
            self._statuslabel.config(text="Please specify a server.")
            return False
        if not self._nick_entry.get():
            self._statuslabel.config(text="Please specify a nickname.")
            return False

        from .backend import NICK_REGEX, CHANNEL_REGEX

        if not re.fullmatch(NICK_REGEX, self._nick_entry.get()):
            self._statuslabel.config(
                text=f"'{self._nick_entry.get()}' is not a valid nickname."
            )
            return False

        # channel entry can be empty, no channels joined
        channels = self._channel_entry.get().split()
        for channel in channels:
            if not re.fullmatch(CHANNEL_REGEX, channel):
                text = f"'{channel}' is not a valid channel name."
                if not channel.startswith(("&", "#", "+", "!")):
                    text += " Usually channel names start with a # character."
                self._statuslabel.config(text=text)
                return False

        try:
            port = int(self._port_entry.get())
            if port <= 0:
                raise ValueError
        except ValueError:
            self._statuslabel.config(text="The port must be a positive integer.")
            return False

        self._statuslabel.config(text="")
        self._connectbutton.config(state="normal")
        return True

    def connect_clicked(self, junk_event: object = None) -> None:
        assert self._validate()
        self.result = {
            "host": self._server_entry.get(),
            "port": int(self._port_entry.get()),
            "ssl": self._ssl_var.get(),
            "nick": self._nick_entry.get(),
            "username": self._nick_entry.get(),
            "realname": self._nick_entry.get(),
            "password": self._password_entry.get() or None,
            "joined_channels": self._channel_entry.get().split(),
            "extra_notifications": [],
        }
        self.winfo_toplevel().destroy()


# returns None if user cancel
def ask_settings_for_new_server(
    transient_to: tkinter.Tk | tkinter.Toplevel | None,
) -> ServerConfig | None:

    dialog = tkinter.Toplevel()
    content = _ConnectDialogContent(dialog)
    content.pack(fill="both", expand=True)

    dialog.title("Connect to IRC server")
    dialog.minsize(350, 200)
    if transient_to is not None:
        dialog.transient(transient_to)

    dialog.wait_window()
    return content.result
