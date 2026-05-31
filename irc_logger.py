from __future__ import annotations

import argparse
import re
import signal
import sys
import time
from base64 import b64encode
from getpass import getuser
from pathlib import Path
from typing import Iterable

from . import backend, config

DEFAULT_LOG_DIR = Path("logs")


def parse_channel_list(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        for part in re.split(r"[\s,]+", value.strip()):
            if part:
                result.append(part)
    return result


def normalize_log_dir(path: str) -> Path:
    log_dir = Path(path)
    if not log_dir.is_absolute():
        log_dir = Path.cwd() / log_dir
    log_dir = log_dir.resolve()
    cwd = Path.cwd().resolve()
    if cwd not in log_dir.parents and log_dir != cwd:
        raise argparse.ArgumentTypeError(
            f"log dir must be under current working directory: {path}"
        )
    return log_dir


class LogManager:
    def __init__(self, root_dir: Path, server_name: str) -> None:
        self.root_dir = root_dir
        self.server_name = server_name
        self._handles: dict[str, tuple[Path, object]] = {}
        self.root_dir.mkdir(parents=True, exist_ok=True)

    def _open_file(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        return path.open("a", encoding="utf-8")

    def _path_for_channel(self, channel: str) -> Path:
        safe_channel = re.sub(r"[^A-Za-z0-9._-]", "_", channel.lstrip("#"))
        if not safe_channel:
            safe_channel = "channel"
        return self.root_dir / self.server_name / f"{safe_channel}.log"

    def _path_for_server_log(self) -> Path:
        return self.root_dir / self.server_name / "server.log"

    def write_server(self, sender: str, text: str) -> None:
        self._write_line(self._path_for_server_log(), sender, text)

    def write_channel(self, channel: str, sender: str, text: str) -> None:
        self._write_line(self._path_for_channel(channel), sender, text)

    def _write_line(self, path: Path, sender: str, text: str) -> None:
        file_handle = self._handles.get(path)
        if file_handle is None:
            file_handle = self._open_file(path)
            self._handles[path] = file_handle
        print(time.asctime(), sender, text, sep="\t", file=file_handle, flush=True)

    def close(self) -> None:
        for handle in self._handles.values():
            try:
                handle.close()
            except OSError:
                pass


class IrcLoggerBot:
    def __init__(self, settings: config.ServerSettings, log_dir: Path, verbose: bool) -> None:
        self.settings = settings
        self.core = backend.IrcCore(settings, verbose=verbose)
        self.log_dir = log_dir
        safe_server_name = re.sub(r"[^A-Za-z0-9._-]", "_", self.settings.host)
        self.log_manager = LogManager(log_dir, safe_server_name)
        self._joined_channels = False
        self._stop_requested = False

    def request_stop(self, *_args: object) -> None:
        self._stop_requested = True

    def run(self) -> int:
        print(f"Log directory: {self.log_dir}")
        signal.signal(signal.SIGINT, self.request_stop)
        signal.signal(signal.SIGTERM, self.request_stop)

        try:
            while not self._stop_requested:
                self.core.run_one_step()
                for event in self.core.get_events():
                    self.handle_event(event)
                if self.core.quitting_finished():
                    break
                time.sleep(0.05)
        except KeyboardInterrupt:
            self._stop_requested = True
        finally:
            self.core.quit(wait=True)
            self.log_manager.close()
        return 0

    def handle_event(self, event: backend.IrcEvent) -> None:
        if isinstance(event, backend.MessageFromServer):
            if event.command == "CAP":
                self._handle_cap(event)
            else:
                self._handle_server_message(event)
        elif isinstance(event, backend.MessageFromUser):
            self._handle_user_message(event)
        elif isinstance(event, backend.ConnectivityMessage):
            self.log_manager.write_server("CONNECTIVITY", event.message)
        elif isinstance(event, backend.HostChanged):
            self.log_manager.write_server("HOST", f"{event.old} -> {event.new}")
        elif isinstance(event, backend.SentPrivmsg):
            self.log_manager.write_server("SENT", f"{event.nick_or_channel}: {event.text}")

    def _handle_cap(self, event: backend.MessageFromServer) -> None:
        if len(event.args) < 2:
            self.log_manager.write_server("CAP", f"Unexpected CAP response: {' '.join(event.args)}")
            return

        subcommand = event.args[1]
        if subcommand == "ACK":
            acknowledged = event.args[-1].split()
            self.core.pending_cap_count -= len(acknowledged)
            for capability in acknowledged:
                self.core.cap_list.add(capability)
            if "sasl" in acknowledged and self.settings.password is not None:
                self._send_authenticate()
        elif subcommand == "NAK":
            rejected = event.args[-1].split()
            self.core.pending_cap_count -= len(rejected)
            if "sasl" in rejected:
                raise ValueError("The server does not support SASL.")
        else:
            self.core.send("CAP END")
            self.log_manager.write_server("CAP", f"Invalid CAP response: {' '.join(event.args)}")
            return

        if self.core.pending_cap_count == 0 and "sasl" not in self.core.cap_list:
            self.core.send("CAP END")

    def _send_authenticate(self) -> None:
        query = f"\0{self.settings.username}\0{self.settings.password}"
        b64_query = b64encode(query.encode("utf-8")).decode("utf-8")
        for i in range(0, len(b64_query), 400):
            self.core.send("AUTHENTICATE " + b64_query[i : i + 400])

    def _handle_server_message(self, event: backend.MessageFromServer) -> None:
        if event.command == "001":
            self._join_channels()
        if event.args:
            target = event.args[0]
            if target in self.settings.joined_channels:
                self.log_manager.write_channel(target, event.server, " ".join(event.args[1:]).strip())
            else:
                self.log_manager.write_server(event.server, f"{event.command} {' '.join(event.args)}")
        else:
            self.log_manager.write_server(event.server, event.command)

    def _humanize_user_event(self, event: backend.MessageFromUser) -> tuple[str, str, str | None]:
        sender = event.sender_nick
        text = ""
        channel: str | None = None

        if event.command == "PRIVMSG" and len(event.args) >= 2:
            channel = event.args[0]
            text = event.args[1]
        elif event.command == "JOIN" and event.args:
            channel = event.args[0]
            text = "has joined"
        elif event.command == "PART" and event.args:
            channel = event.args[0]
            text = "has left" if len(event.args) == 1 else f"has left ({event.args[1]})"
        elif event.command == "QUIT":
            text = "quit" if not event.args else f"quit ({event.args[0]})"
        elif event.command == "NICK" and event.args:
            text = f"is now known as {event.args[0]}"
        elif event.command == "KICK" and len(event.args) >= 2:
            channel = event.args[0]
            victim = event.args[1]
            reason = event.args[2] if len(event.args) >= 3 else ""
            text = f"kicked {victim}" + (f" ({reason})" if reason else "")
        else:
            if event.args:
                text = f"{event.command} {' '.join(event.args)}"
            else:
                text = event.command

        return sender, text, channel

    def _handle_user_message(self, event: backend.MessageFromUser) -> None:
        sender, text, channel = self._humanize_user_event(event)
        if channel is not None and channel in self.settings.joined_channels:
            self.log_manager.write_channel(channel, sender, text)
        else:
            self.log_manager.write_server(sender, text)

    def _join_channels(self) -> None:
        if self._joined_channels:
            return
        for channel in self.settings.joined_channels:
            self.core.send(f"JOIN {channel}")
            self.log_manager.write_server("JOIN", f"Joining {channel}")
        self._joined_channels = True


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run an IRC logger bot using mantaray backend code."
    )
    parser.add_argument("--server", default="irc.libera.chat", help="IRC server host")
    parser.add_argument("--port", type=int, default=6697, help="IRC server port")
    parser.add_argument(
        "--ssl",
        dest="ssl",
        action="store_true",
        default=True,
        help="Use SSL/TLS for the connection",
    )
    parser.add_argument(
        "--no-ssl",
        dest="ssl",
        action="store_false",
        help="Do not use SSL/TLS for the connection",
    )
    parser.add_argument("--nick", default=getuser(), help="Nickname to use")
    parser.add_argument("--username", default=getuser(), help="Username to use")
    parser.add_argument("--realname", help="Real name to use (defaults to username)")
    parser.add_argument("--password", help="Password for server authentication", default=None)
    parser.add_argument(
        "--channel",
        dest="channels",
        action="append",
        default=[],
        help="Channel to join. May be repeated.",
    )
    parser.add_argument(
        "--channels",
        dest="channels_str",
        help="Comma- or space-separated list of channels to join.",
    )
    parser.add_argument(
        "--log-dir",
        default=str(DEFAULT_LOG_DIR),
        type=normalize_log_dir,
        help="Directory for log files, relative to the current working directory.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print raw IRC send/receive debugging information.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    channels: list[str] = []
    if args.channels:
        channels.extend(args.channels)
    if args.channels_str is not None:
        channels.extend(parse_channel_list([args.channels_str]))

    if not channels:
        parser.error("At least one channel must be provided via --channel or --channels")

    args.channels = channels

    realname = args.realname or args.username
    server_settings = config.ServerSettings(
        dict_from_file={
            "host": args.server,
            "port": args.port,
            "ssl": args.ssl,
            "nick": args.nick,
            "username": args.username,
            "realname": realname,
            "password": args.password,
            "joined_channels": args.channels,
        }
    )

    bot = IrcLoggerBot(server_settings, args.log_dir, args.verbose)
    return bot.run()


if __name__ == "__main__":
    raise SystemExit(main())
