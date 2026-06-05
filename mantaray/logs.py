from __future__ import annotations

import os
import io
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import IO
from collections.abc import Iterator

from mantaray import views, received


@dataclass
class _Log:
    server_name: str
    channel_or_nick: str | None  # nick in DMs, None if this is the server
    path: Path
    file: IO[str]


# We can't read the whole file from start because it can be huge.
def _read_file_backwards(file: IO[bytes], *, chunk_size: int = 1_000_000) -> Iterator[bytes]:
    file.seek(0, io.SEEK_END)  # Go to end of file
    pos = file.tell()
    first = True
    remaining = b""

    while pos > 0:
        # Don't read more data than available
        actual_chunk_size = min(chunk_size, pos)
        pos -= actual_chunk_size
        assert pos >= 0
        file.seek(pos)
        chunk = file.read(actual_chunk_size)

        remaining, *whole_lines = (chunk + remaining).split(b"\n")
        for line in reversed(whole_lines):
            line_string = line.rstrip(b"\r")
            # File typically ends with "bla bla bla\n" or "bla bla bla\r\n"
            # That doesn't mean we should produce an empty string.
            if line_string or not first:
                yield line_string
            first = False

    line_string = remaining.rstrip(b"\r")
    if line_string or not first:
        yield line_string


def _parse_normal_line(line: str) -> tuple[datetime, str | None, str]:
    # It's important to specify maxsplit here because the message may contain
    # tab characters. (I think?)
    timestamp, sender, message = line.split("\t", maxsplit=2)
    return (
        datetime.fromisoformat(timestamp),
        None if sender == "*" else sender,
        message,
    )


class LogManager:
    def __init__(self, log_dir: Path):
        self.log_dir = log_dir
        self._id_counter = 0

        # Data structure chosen so that accessing by log_id is fast, since
        # that's by far most common.
        self._opened_logs: dict[int, _Log] = {}

    def _make_path(self, server_name: str, channel_or_nick: str | None) -> Path:
        # This assumes that server names do not collide. Good enough for now...
        #
        # If channel names collide, or nicks collide with the special "server"
        # string, we just put everything to the same file and write the actual
        # unsanitized name inside the file.
        safe_folder = re.sub("[^A-Za-z0-9-_#]", "_", server_name.lower())
        safe_file = re.sub("[^A-Za-z0-9-_#]", "_", (channel_or_nick or "server").lower())
        return self.log_dir / safe_folder / f"{safe_file}.log"

    # Returns an integer ID.
    #
    # This doesn't hand out the file object directly. By forcing file writing
    # to happen here, it remains easy to figure out what exactly the file
    # format is.
    def open_log_file(self, server_name: str, channel_or_nick: str | None) -> int:
        path = self._make_path(server_name, channel_or_nick)

        file = None
        for log in self._opened_logs.values():
            if log.path == path:
                # It all goes to the same file. For example, if someone's nick
                # is "server", the only thing distinguishing that from the
                # server's logs is information in each logged line.
                file = log.file

        if file is None:
            path.parent.mkdir(parents=True, exist_ok=True)
            file = path.open("a", encoding="utf-8")
            if sys.platform != "win32":
                path.chmod(0o600)
            print("\n\n*** LOGGING BEGINS", datetime.now().astimezone().isoformat(), file=file, flush=True)

        self._id_counter += 1
        log_id = self._id_counter

        assert log_id not in self._opened_logs
        self._opened_logs[log_id] = _Log(
            server_name=server_name,
            channel_or_nick=channel_or_nick,
            path=path,
            file=file,
        )
        return log_id

    def close_log_file(self, log_id: int) -> None:
        log = self._opened_logs.pop(log_id)
        if log.file not in (other_log.file for other_log in self._opened_logs.values()):
            print("*** LOGGING ENDS", datetime.now().astimezone().isoformat(), file=log.file, flush=True)
            log.file.close()

    # Sender is None for "system" messages like join/leave that show up with "*" in UI
    #
    # Log file format was designed to be both easy to parse and human-readable.
    # Every line has a timestamp so that you can easily find the logs you care
    # about for showing older messages in the UI.
    def write_to_log(self, log_id: int, timestamp: datetime, sender: str | None, message: str) -> None:
        log = self._opened_logs[log_id]

        if log.channel_or_nick is None:
            channel_or_nick_text = "(server)"
        else:
            # Someone's nick could be "server" or even "[".
            # This cannot match the server's text regardless of what the nick is.
            channel_or_nick_text = "[" + log.channel_or_nick + "]"

        if sender is None:
            sender_text = "*"
        else:
            sender_text = '<' + sender + '>'

        # Make sure the log file can be parsed by treating each line as
        # space-separated components, except for the message at the end.
        assert " " not in channel_or_nick_text
        assert " " not in sender_text

        assert timestamp.tzinfo is not None
        print(timestamp.isoformat(), channel_or_nick_text, sender_text, message, file=log.file, flush=True)

    # This can be used to fetch:
    #   - messages and other events (e.g. joining, kicking) that happened on a channel
    #   - previous DMs with another user
    #
    # This cannot be used to fetch the log of the server itself. Those logs are
    # not as useful as chat messages and we don't really care about them.
    def read_old_logs(
        self,
        server_name: str,
        channel_or_nick: str,  # cannot be None which would mean the server itself
        since: datetime,
    ) -> list[tuple[datetime, str | None, str]]:
        path = self._make_path(server_name, channel_or_nick)

        # Windows doesn't allow opening the same file twice, so make sure
        # we don't read a thing that is already open.
        if path in (opened_log.path for opened_log in self._opened_logs.values()):
            return []

        results: list[tuple[datetime, str | None, str]] = []

        try:
            with path.open("rb") as f:
                for line_bytes in _read_file_backwards(f):
                    line = line_bytes.decode("utf-8", errors="replace")

                    try:
                        if line.startswith("***") or not line:
                            continue

                        # Examples:
                        #
                        #   2026-06-05T13:14:39.354697+03:00 [Bob] <Bob> hello friends
                        #   --> ('2026-06-05T13:14:39.354697+03:00', 'Bob', 'Bob', 'hello friends')
                        #
                        #   2026-06-05T13:18:49.976094+03:00 (server) * 318 Bob End of /WHOIS list.
                        #   --> ('2026-06-05T13:18:49.976094+03:00', None, None, '318 Bob End of /WHOIS list.')
                        m = re.fullmatch(r'([0-9T:.+-]+) (?:\(server\)|\[(\S+)\]) (?:\*|<(\S+)>) (.*)', line)
                        if m is None:
                            raise ValueError

                        timestamp_string, line_channel_or_nick, sender, message = m.groups()
                        timestamp = datetime.fromisoformat(timestamp_string)
                        if timestamp < since:
                            # So old that we don't care about it
                            if timestamp < since - timedelta(hours=24):
                                # So old that it's safe to assume everything
                                # before this line is also too old, even if
                                # computer's clock was adjusted.
                                break
                            continue

                        if line_channel_or_nick == channel_or_nick:
                            # We care about this
                            results.append((timestamp, sender, message))

                    except ValueError:
                        print(f"Cannot parse IRC log line in {path}: {line}")

        except FileNotFoundError:
            pass

        except OSError as e:
            print(f"Failed to read log from {path}: {e}")

        results.reverse()
        return results


def read_old_logs(view: views.ChannelView | views.PMView) -> None:
    assert view.log_id is None  # TODO: read logs dynamically when scrolling up?

    if not view.server_view.settings.read_logs:
        return

    if isinstance(view, views.ChannelView):
        channel_or_nick = view.channel_name
    else:
        channel_or_nick = view.nick_of_other_user

    now = datetime.now().astimezone()
    old_logs = view.irc_widget.log_manager.read_old_logs(
        server_name=view.server_view.view_name,
        channel_or_nick=channel_or_nick,
        since=(now - timedelta(days=1)),  # TODO: make this configurable?
    )

    old_end = view.textwidget.index("end - 1 char")

    for timestamp, sender, message in old_logs:
        if sender is None:
            view.add_message(message, timestamp=timestamp)
        else:
            received.add_received_privmsg_to_view(view, sender, message, timestamp=timestamp, already_seen=True)

    view.textwidget.tag_add("from-log", old_end, "end - 1 char")


def start_logging(view: views.View) -> None:
    if view.log_id is not None:
        # already logging
        return

    if not view.server_view.settings.logging:
        # user doesn't want us to log anything
        return

    if isinstance(view, views.ChannelView):
        channel_or_nick = view.channel_name
    elif isinstance(view, views.PMView):
        channel_or_nick = view.nick_of_other_user
    else:
        assert isinstance(view, views.ServerView)
        channel_or_nick = None

    assert view.log_id is None
    view.log_id = view.irc_widget.log_manager.open_log_file(view.server_view.view_name, channel_or_nick)


def stop_logging(view: views.View) -> None:
    if view.log_id is not None:
        view.irc_widget.log_manager.close_log_file(view.log_id)
        view.log_id = None
