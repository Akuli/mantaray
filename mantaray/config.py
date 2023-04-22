from __future__ import annotations

import json
import re
import sys
import tkinter
from getpass import getuser
from pathlib import Path
from tkinter import ttk
from tkinter.font import Font
from typing import TYPE_CHECKING, Any

if sys.version_info >= (3, 8):
    from typing import TypedDict
else:
    if TYPE_CHECKING:
        from typing_extensions import TypedDict
    else:
        TypedDict = object


# requires tkinter root window to exist
def get_default_fixed_font() -> tuple[str, int]:
    font = Font(name="TkFixedFont", exists=True)
    return (font["family"], font["size"])


class Settings:
    def __init__(self, config_dir: Path, *, read_only: bool = False) -> None:
        self._config_dir = config_dir
        self.read_only = read_only

        default_family, default_size = get_default_fixed_font()
        self.font = Font(family=default_family, size=default_size)
        self.servers: list[ServerSettings] = []
        self.view_selector_width = 200
        self.userlist_width = 150
        self.theme = "dark"

    def add_server(self, server_settings: ServerSettings) -> None:
        assert server_settings.parent_settings_object is None
        server_settings.parent_settings_object = self
        self.servers.append(server_settings)

    def load(self) -> None:
        assert not self.servers  # not loaded yet

        with (self._config_dir / "config.json").open("r", encoding="utf-8") as file:
            result = json.load(file)

            if "font_family" in result and "font_size" in result:
                self.font.config(family=result["font_family"], size=result["font_size"])
            if "view_selector_width" in result:
                self.view_selector_width = result["view_selector_width"]
            if "userlist_width" in result:
                self.userlist_width = result["userlist_width"]
            if "theme" in result:
                self.theme = result["theme"]

            for server_dict in result["servers"]:
                # Backwards compatibility with older config.json files
                server_dict.setdefault("ssl", True)
                server_dict.setdefault("password", None)
                server_dict.setdefault("extra_notifications", [])
                server_dict.setdefault("audio_notification", False)
                server_dict.setdefault(
                    "join_leave_hiding",
                    {"show_by_default": True, "exception_nicks": []},
                )
                server_dict.setdefault("last_away_status", "Away")

                self.servers.append(
                    ServerSettings(
                        dict_from_file=server_dict, parent_settings_object=self
                    )
                )

        if not self.font.metrics("fixed"):
            self.font.config(family=get_default_fixed_font()[0])
            self.save()

    def get_json(self) -> dict[str, Any]:
        return {
            "font_family": self.font["family"],
            "font_size": self.font["size"],
            "servers": [s.get_json() for s in self.servers],
            "view_selector_width": self.view_selector_width,
            "userlist_width": self.userlist_width,
            "theme": self.theme,
        }

    # Please save the settings after changing them.
    def save(self) -> None:
        if self.read_only:
            return

        self._config_dir.mkdir(parents=True, exist_ok=True)
        with (self._config_dir / "config.json").open("w", encoding="utf-8") as file:
            json.dump(self.get_json(), file, indent=2)
            file.write("\n")

        # config.json contains passwords (hexchat stores them in plain text too)
        # TODO: how do permissions work on windows?
        if sys.platform != "win32":
            (self._config_dir / "config.json").chmod(0o600)


class JoinLeaveHidingSettings(TypedDict):
    show_by_default: bool
    exception_nicks: list[str]


class ServerSettings:
    def __init__(
        self,
        parent_settings_object: Settings | None = None,
        dict_from_file: dict[str, Any] = {},
    ) -> None:
        self.parent_settings_object = parent_settings_object

        # The defaults passed to .get() are what the user sees when running Mantaray
        # for the first time.
        self.host: str = dict_from_file.get("host", "irc.libera.chat")
        self.port: int = dict_from_file.get("port", 6697)
        self.ssl: bool = dict_from_file.get("ssl", True)
        self.nick: str = dict_from_file.get("nick", getuser())
        self.username: str = dict_from_file.get("username", getuser())
        self.realname: str = dict_from_file.get("realname", getuser())
        self.password: str | None = dict_from_file.get("password", None)
        self.joined_channels: list[str] = dict_from_file.get(
            "joined_channels", ["##learnpython"]
        )
        self.extra_notifications: set[str] = set(
            dict_from_file.get("extra_notifications", [])
        )
        self.join_leave_hiding: JoinLeaveHidingSettings = dict_from_file.get(
            "join_leave_hiding", {"show_by_default": True, "exception_nicks": []}
        )
        self.audio_notification: bool = dict_from_file.get("audio_notification", False)
        self.last_away_status: str = dict_from_file.get(
            "last_away_status", "Away"
        )  # not empty

    def get_json(self) -> dict[str, Any]:
        return {
            "host": self.host,
            "port": self.port,
            "ssl": self.ssl,
            "nick": self.nick,
            "username": self.username,
            "realname": self.realname,
            "password": self.password,
            "joined_channels": self.joined_channels,
            "extra_notifications": list(self.extra_notifications),
            "join_leave_hiding": self.join_leave_hiding,
            "audio_notification": self.audio_notification,
            "last_away_status": self.last_away_status,
        }

    # Please save the settings after changing them.
    def save(self) -> None:
        if self.parent_settings_object is not None:
            self.parent_settings_object.save()


class _EntryWithVar(ttk.Entry):
    def __init__(self, master: tkinter.Misc, **kwargs: Any):
        var = tkinter.StringVar()
        super().__init__(master, textvariable=var, **kwargs)
        self.var = var


class _JoinLeaveWidget(ttk.Frame):
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
        self._hide_these_users_entry.pack(fill="x", padx=(20, 50))
        ttk.Radiobutton(
            self,
            variable=self._show_by_default_var,
            value=False,
            text="Hide join/part/quit messages for all nicks except:",
        ).pack(fill="x")
        self._show_these_users_entry.pack(fill="x", padx=(20, 50))

        self._show_by_default_var.trace_add("write", self._update_entry_disableds)

    def _update_entry_disableds(self, *junk: object) -> None:
        if self._show_by_default_var.get():
            self._show_these_users_entry.config(state="disabled")
            self._hide_these_users_entry.config(state="normal")
        else:
            self._show_these_users_entry.config(state="normal")
            self._hide_these_users_entry.config(state="disabled")

    def set_from_config(self, config: JoinLeaveHidingSettings) -> None:
        self._show_by_default_var.set(config["show_by_default"])
        if config["show_by_default"]:
            self._hide_these_users_entry.var.set(" ".join(config["exception_nicks"]))
        else:
            self._show_these_users_entry.var.set(" ".join(config["exception_nicks"]))

    def get_config(self) -> JoinLeaveHidingSettings:
        if self._show_by_default_var.get():
            exceptions = self._hide_these_users_entry.get().split()
        else:
            exceptions = self._show_these_users_entry.get().split()
        return {
            "show_by_default": self._show_by_default_var.get(),
            "exception_nicks": exceptions,
        }


class _DialogContent(ttk.Frame):
    def __init__(
        self,
        master: tkinter.Misc,
        settings: ServerSettings,
        connecting_to_new_server: bool,
    ):
        super().__init__(master)
        self._settings = settings
        self.user_clicked_connect_or_reconnect = False

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
            self._join_part_quit = _JoinLeaveWidget(self)
            self._join_part_quit.grid(
                row=self._rownumber, column=0, columnspan=3, sticky="we"
            )
            self._rownumber += 1

        self._audio_var = tkinter.BooleanVar(value=settings.audio_notification)
        if connecting_to_new_server:
            self.audio_notification_checkbox = None
        else:
            self.audio_notification_checkbox = ttk.Checkbutton(
                self, text="Enable audio notification on ping", variable=self._audio_var
            )
            self.audio_notification_checkbox.grid(
                row=self._rownumber, column=0, sticky="w", padx=5, pady=10
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
        self._server_entry.var.set(settings.host)
        self._ssl_var.set(settings.ssl)  # must be before port
        self._port_entry.var.set(str(settings.port))
        if self._nick_entry is not None:
            self._nick_entry.var.set(settings.nick)
        if self._username_entry is not None:
            self._username_entry.var.set(settings.username)
        if self._realname_entry is not None:
            self._realname_entry.var.set(settings.realname)
        self._password_entry.var.set(settings.password or "")
        if self._channel_entry is not None:
            self._channel_entry.var.set(" ".join(settings.joined_channels))
        if self._join_part_quit is not None:
            self._join_part_quit.set_from_config(settings.join_leave_hiding)

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

        from .backend import CHANNEL_REGEX, NICK_REGEX

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

        self._settings.host = self._server_entry.get()
        self._settings.port = int(self._port_entry.get())
        self._settings.ssl = self._ssl_var.get()
        if self._nick_entry is not None:
            self._settings.nick = self._nick_entry.get()
        self._settings.username = (
            self._settings.nick
            if self._username_entry is None
            else self._username_entry.get()
        )
        self._settings.realname = (
            self._settings.nick
            if self._realname_entry is None
            else self._realname_entry.get()
        )
        self._settings.password = self._password_entry.get() or None
        if self._channel_entry is not None:
            self._settings.joined_channels = self._channel_entry.get().split()
        self._settings.audio_notification = self._audio_var.get()
        if self._join_part_quit is not None:
            self._settings.join_leave_hiding = self._join_part_quit.get_config()

        self._settings.save()
        self.user_clicked_connect_or_reconnect = True
        self.winfo_toplevel().destroy()


# Returns True when user clicks connect/reconnect, False if cancel (settings unchanged).
def show_connection_settings_dialog(
    settings: ServerSettings,
    connecting_to_new_server: bool,
    transient_to: tkinter.Tk | tkinter.Toplevel | None,
) -> bool:
    dialog = tkinter.Toplevel()
    content = _DialogContent(
        dialog, settings, connecting_to_new_server=connecting_to_new_server
    )

    if connecting_to_new_server:
        dialog.title("Connect to an IRC server")
        dialog.minsize(450, 200)
    else:
        dialog.title("Server settings")
        dialog.minsize(450, 300)

    content.pack(fill="both", expand=True)
    if transient_to is not None:
        dialog.transient(transient_to)
    dialog.wait_window()
    return content.user_clicked_connect_or_reconnect
