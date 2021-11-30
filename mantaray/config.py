from __future__ import annotations
import json
import re
import tkinter
import sys
from pathlib import Path
from tkinter import ttk
from tkinter.font import Font
from getpass import getuser
from typing import Any, TYPE_CHECKING

if sys.version_info >= (3, 8):
    from typing import TypedDict
else:
    if TYPE_CHECKING:
        from typing_extensions import TypedDict
    else:
        TypedDict = object


class JoinLeaveHidingConfig(TypedDict):
    show_by_default: bool
    exception_nicks: list[str]


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
    join_leave_hiding: JoinLeaveHidingConfig


class Config(TypedDict):
    servers: list[ServerConfig]
    font_family: str
    font_size: int


# requires tkinter root window to exist
def get_default_fixed_font() -> tuple[str, int]:
    font = Font(name="TkFixedFont", exists=True)
    return (font["family"], font["size"])


def load_from_file(config_dir: Path) -> Config | None:
    try:
        with (config_dir / "config.json").open("r", encoding="utf-8") as file:
            result = json.load(file)
            # Backwards compatibility with older config.json files
            for server in result["servers"]:
                server.setdefault("ssl", True)
                server.setdefault("password", None)
                server.setdefault("extra_notifications", [])
                server.setdefault("join_leave_hiding", {
                    "show_by_default": True,
                    "exception_nicks": [],
                })
            if "font_family" not in result or "font_size" not in result:
                result["font_family"], result["font_size"] = get_default_fixed_font()
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


class _JoinPartQuitSettings(ttk.Frame):
    def __init__(self, master: tkinter.Misc):
        super().__init__(master)
        self._show_by_default_var = tkinter.BooleanVar()
        self._show_these_users_entry = _EntryWithVar(self)
        self._hide_these_users_entry = _EntryWithVar(self)

        ttk.Radiobutton(
            self,
            variable=self._show_by_default_var,
            value=True,
            text="Show join/part/quit messages for all nicks except:",
        ).pack(fill="x")
        self._hide_these_users_entry.pack(fill="x")
        ttk.Radiobutton(
            self,
            variable=self._show_by_default_var,
            value=False,
            text="Hide join/part/quit messages for all nicks except:",
        ).pack(fill="x")
        self._show_these_users_entry.pack(fill="x")

        self._show_by_default_var.trace_add("write", self._update_entry_disableds)

    def _update_entry_disableds(self, *junk: object) -> None:
        if self._show_by_default_var.get():
            self._show_these_users_entry.config(state="disabled")
            self._hide_these_users_entry.config(state="normal")
        else:
            self._show_these_users_entry.config(state="normal")
            self._hide_these_users_entry.config(state="disabled")

    def set_from_config(self, config: JoinLeaveHidingConfig) -> None:
        self._show_by_default_var.set(config["show_by_default"])
        if config["show_by_default"]:
            self._hide_these_users_entry.var.set(
                " ".join(config["exception_nicks"])
            )
        else:
            self._show_these_users_entry.var.set(
                " ".join(config["exception_nicks"])
            )

    def get_config(self) -> JoinLeaveHidingConfig:
        if self._show_by_default_var.get():
            exceptions = self._hide_these_users_entry.get().split()
        else:
            exceptions = self._show_these_users_entry.get().split()
        return {"show_by_default": self._show_by_default_var.get(), "exception_nicks": exceptions}


class _DialogContent(ttk.Frame):
    def __init__(
        self,
        master: tkinter.Misc,
        initial_config: ServerConfig,
        connecting_to_new_server: bool,
    ):
        super().__init__(master)
        self._initial_config = initial_config
        self.result: ServerConfig | None = None

        self._rownumber = 0
        self.grid_columnconfigure(0, minsize=60)
        self.grid_columnconfigure(1, weight=1)

        self._server_entry = self._create_entry()
        self._add_row("Server:", self._server_entry)

        ttk.Label(self, text="Port:").grid(row=self._rownumber, column=0, sticky="w")
        self._port_entry = self._create_entry(width=8)
        self._port_entry.grid(row=self._rownumber, column=1, sticky="we")
        self._setup_entry_bindings(self._port_entry)
        self._ssl_var = tkinter.BooleanVar(value=True)
        self._ssl_var.trace("w", self._guess_port_based_on_ssl)
        self._ssl_checkbox = ttk.Checkbutton(
            self, text="Use SSL", variable=self._ssl_var
        )
        self._ssl_checkbox.grid(row=self._rownumber, column=2)
        self._rownumber += 1

        if not connecting_to_new_server:
            self._channel_entry = None
        else:
            self._channel_entry = self._create_entry()
            self._add_row("Channels (space-separated):", self._channel_entry)

        if not connecting_to_new_server:
            self._nick_entry = None
        else:
            self._nick_entry = self._create_entry()
            self._add_row("Nickname:", self._nick_entry)

        if connecting_to_new_server:
            self._username_entry = None
        else:
            self._username_entry = self._create_entry()
            self._add_row("Username:", self._username_entry)

        self._password_entry = self._create_entry(show="*")
        self._add_row("Password (leave empty if none):", self._password_entry)

        if connecting_to_new_server:
            self._realname_entry = None
        else:
            self._realname_entry = self._create_entry()
            self._add_row("Real* name:", self._realname_entry)

        if connecting_to_new_server:
            self._join_part_quit = None
        else:
            self._join_part_quit = _JoinPartQuitSettings(self)
            self._join_part_quit.grid(
                row=self._rownumber, column=0, columnspan=3, sticky="we"
            )
            self._rownumber += 1

        if self._realname_entry is not None:
            infolabel = ttk.Label(
                self,
                text=(
                    "* This doesn't need to be your real name.\n"
                    "   You can set this to anything you want."
                ),
            )
            infolabel.grid(
                row=self._rownumber, column=0, columnspan=3, sticky="w", padx=5, pady=5
            )
            self._rownumber += 1

        self._statuslabel = ttk.Label(self)
        self._statuslabel.grid(
            row=self._rownumber, column=0, columnspan=3, pady=5, sticky="swe"
        )
        self._statuslabel.bind(
            "<Configure>",
            lambda event: self._statuslabel.config(wraplength=event.width),
        )
        self.grid_rowconfigure(self._rownumber, weight=1)
        self._rownumber += 1

        self._buttonframe = ttk.Frame(self)
        self._buttonframe.grid(
            row=self._rownumber, column=0, columnspan=3, padx=5, pady=5, sticky="we"
        )
        ttk.Button(self._buttonframe, text="Cancel", command=self.cancel).pack(
            side="right"
        )
        self._connectbutton = ttk.Button(
            self._buttonframe,
            text=("Connect!" if connecting_to_new_server else "Reconnect"),
            command=self.connect_clicked,
        )
        self._connectbutton.pack(side="right")

        # now everything's ready for _validate()
        self._server_entry.var.set(initial_config["host"])
        self._ssl_var.set(initial_config["ssl"])  # must be before port
        self._port_entry.var.set(str(initial_config["port"]))
        if self._nick_entry is not None:
            self._nick_entry.var.set(initial_config["nick"])
        if self._username_entry is not None:
            self._username_entry.var.set(initial_config["username"])
        if self._realname_entry is not None:
            self._realname_entry.var.set(initial_config["realname"])
        self._password_entry.var.set(initial_config["password"] or "")
        if self._channel_entry is not None:
            self._channel_entry.var.set(" ".join(initial_config["joined_channels"]))
        if self._join_part_quit is not None:
            self._join_part_quit.set_from_config(initial_config["join_leave_hiding"])

    def _create_entry(self, **kwargs: Any) -> _EntryWithVar:
        entry = _EntryWithVar(self, **kwargs)
        entry.var.trace("w", self._validate)
        return entry

    def _setup_entry_bindings(self, entry: ttk.Entry) -> None:
        # TODO: bind on the whole window instead
        entry.bind("<Return>", self.connect_clicked, add=True)
        entry.bind("<Escape>", self.cancel, add=True)

    def _add_row(self, label: str, widget: ttk.Entry) -> None:
        ttk.Label(self, text=label).grid(row=self._rownumber, column=0, sticky="w")
        widget.grid(row=self._rownumber, column=1, columnspan=2, sticky="we")
        self._setup_entry_bindings(widget)
        self._rownumber += 1

    def _guess_port_based_on_ssl(self, *junk: object) -> None:
        self._port_entry.var.set("6697" if self._ssl_var.get() else "6667")

    def _validate(self, *junk: object) -> bool:
        self._connectbutton.config(state="disabled")

        if not self._server_entry.get():
            self._statuslabel.config(text="Please specify a server.")
            return False
        if self._nick_entry is not None and not self._nick_entry.get():
            self._statuslabel.config(text="Please specify a nickname.")
            return False
        if self._username_entry is not None and not self._username_entry.get():
            self._statuslabel.config(text="Please specify a username.")
            return False
        # TODO: can realname be empty?

        from .backend import NICK_REGEX, CHANNEL_REGEX

        if self._nick_entry is not None and not re.fullmatch(
            NICK_REGEX, self._nick_entry.get()
        ):
            self._statuslabel.config(
                text=f"'{self._nick_entry.get()}' is not a valid nickname."
            )
            return False

        if self._channel_entry is not None:
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

    def cancel(self, junk_event: object = None) -> None:
        self.winfo_toplevel().destroy()

    def connect_clicked(self, junk_event: object = None) -> None:
        assert self._validate()
        if self._nick_entry is None:
            nick = self._initial_config["nick"]
        else:
            nick = self._nick_entry.get()

        self.result = {
            "host": self._server_entry.get(),
            "port": int(self._port_entry.get()),
            "ssl": self._ssl_var.get(),
            "nick": nick,
            "username": (
                nick if self._username_entry is None else self._username_entry.get()
            ),
            "realname": (
                nick if self._realname_entry is None else self._realname_entry.get()
            ),
            "password": self._password_entry.get() or None,
            "joined_channels": (
                self._initial_config["joined_channels"]
                if self._channel_entry is None
                else self._channel_entry.get().split()
            ),
            "extra_notifications": self._initial_config["extra_notifications"],
            "join_leave_hiding": (
                self._initial_config["join_leave_hiding"]
                if self._join_part_quit is None
                else self._join_part_quit.get_config()
            )
        }
        self.winfo_toplevel().destroy()


# returns None if user cancel
def show_connection_settings_dialog(
    transient_to: tkinter.Tk | tkinter.Toplevel | None,
    initial_config: ServerConfig | None,
) -> ServerConfig | None:

    dialog = tkinter.Toplevel()

    if initial_config is None:
        content = _DialogContent(
            dialog,
            initial_config={
                "host": "irc.libera.chat",
                "port": 6697,
                "ssl": True,
                "nick": getuser(),
                "username": getuser(),
                "realname": getuser(),
                "password": None,
                "joined_channels": ["##learnpython"],
                "extra_notifications": [],
                "join_leave_hiding": {"show_by_default": True, "exception_nicks": []},
            },
            connecting_to_new_server=True,
        )
        dialog.title("Connect to an IRC server")
        dialog.minsize(450, 200)
    else:
        content = _DialogContent(dialog, initial_config, connecting_to_new_server=False)
        dialog.title("Connection settings")
        dialog.minsize(450, 300)

    content.pack(fill="both", expand=True)
    if transient_to is not None:
        dialog.transient(transient_to)
    dialog.wait_window()
    return content.result
