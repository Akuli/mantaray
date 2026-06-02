import io
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import IO


def make_timestamp() -> str:
    # This includes the timezone and is not locale dependent
    return datetime.now().astimezone().isoformat()


@dataclass
class _Log:
    server_name: str
    channel_or_nick: str | None  # nick if DMs, None means the server itself
    path: Path
    file: IO[str]
    lines_written: int


class LogManager:
    def __init__(self, log_dir: Path):
        self.log_dir = log_dir
        self._id_counter = 0

        # Data structure chosen so that accessing by log_id is fast, since
        # that's by far most common.
        self._opened_logs: dict[int, _Log] = {}

    def _make_path(self, server_name: str, channel_or_nick: str | None, n: int) -> Path:
        # This assumes that server names do not collide. Good enough for now...
        safe_folder = re.sub("[^A-Za-z0-9-_#]", "_", server_name.lower())
        safe_file = re.sub("[^A-Za-z0-9-_#]", "_", (channel_or_nick or "server").lower())

        assert n >= 1
        if n == 1:
            return self.log_dir / safe_folder / f"{safe_file}.log"
        else:
            return self.log_dir / safe_folder / f"{safe_file}({n}).log"

    # Returns an integer ID.
    #
    # This doesn't hand out the file object directly. By forcing file writing
    # to happen here, it remains easy to figure out what exactly the file
    # format is.
    def open_log_file(self, server_name: str, channel_or_nick: str | None) -> int:
        # Logs don't get mixed up even if someone's nick is "server" or
        # multiple names are the same when sanitized.
        n = 1
        while True:
            path = self._make_path(server_name, channel_or_nick, n)
            if not any(log.path == path for log in self._opened_logs.values()):
                break
            n += 1

        path.parent.mkdir(parents=True, exist_ok=True)
        file = path.open("a", encoding="utf-8")
        if sys.platform != "win32":
            path.chmod(0o600)

        print(
            "\n\n*** LOGGING BEGINS",
            make_timestamp(),
            server_name,
            channel_or_nick or "*",
            sep="\t",
            file=file,
            flush=True,
        )

        self._id_counter += 1
        log_id = self._id_counter

        assert log_id not in self._opened_logs
        self._opened_logs[log_id] = _Log(
            server_name=server_name,
            channel_or_nick=channel_or_nick,
            path=path,
            file=file,
            lines_written=0,
        )
        return log_id

    def close_log_file(self, log_id: int) -> None:
        log = self._opened_logs.pop(log_id)
        print(
            "\n\n*** LOGGING ENDS",
            make_timestamp(),
            log.server_name,
            log.channel_or_nick or "*",
            sep="\t",
            file=log.file,
            flush=True,
        )
        log.file.close()

    # Sender is None for "system" messages like join/leave that show up with "*" in UI
    def write_to_log(self, log_id: int, sender: str | None, message: str) -> None:
        log = self._opened_logs[log_id]
        timestamp = make_timestamp()

        print(
            timestamp,
            sender or "*",
            message,
            sep="\t",
            file=log.file,
            flush=True,
        )

        log.lines_written += 1
        if log.lines_written % 1000 == 0:
            # Due to file name sanitization duplicates, the same log file can
            # contain logs from e.g. DMs with multiple different users.
            #
            # Every 1000 lines, add a thing to indicate what the log file contains.
            # Lines are almost always less than 1000 bytes, so this ensures that
            # each 1MB chunk of log has at least one begin, continue or end marker.
            print(
                f"\n\n*** {log.lines_written} lines have been logged since the file was opened.",
                timestamp,
                log.server_name,
                log.channel_or_nick or "*",
                sep="\t",
                file=log.file,
                flush=True,
            )

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
        # Windows doesn't allow opening the same file twice, so make sure
        # we don't read a thing that is already open.
        paths_to_avoid = set()
        for opened_log in self._opened_logs.values():
            assert (server_name, channel_or_nick) != (opened_log.server_name, opened_log.channel_or_nick)
            paths_to_avoid.add(opened_log.path)

        results: list[tuple[datetime, str | None, str]] = []

        # 10 duplicates better be enough!
        for n in range(1, 11):
            path = self._make_path(server_name, channel_or_nick, n)
            if path in paths_to_avoid:
                continue

            try:
                with path.open("rb") as f:
                    # This is tricky. We can't read the whole file from start
                    # because it can be huge. It's better to start from the end
                    # and stop when timestamps are no longer recent enough.
                    f.seek(0, io.SEEK_END)  # Go to end of file
                    pos = f.tell()
                    chunks: list[bytes] = []
                    while pos > 0:
                        # See comments elsewhere in this file to understand why 1MB.
                        actual_chunk_size = min(1_000_000, pos)
                        pos -= actual_chunk_size
                        assert pos >= 0
                        f.seek(pos)
                        chunk = f.read(actual_chunk_size)
                        chunks.append(chunk)
                        # Stop early if we get a chunk that is old enough
                        #
                        # Newlines can be b"\r\n" or b"\n", so we can't look for
                        # b"\n\n***" but we can look for b"\n***". That works
                        # even if the file actually contains b"\r\n***".
                        #
                        # This requires a tab after the timestamp to ensure the
                        # timestamp isn't getting truncated.
                        #
                        # This does not handle markers that are split into two
                        # chunks, and that's fine because markers occur so
                        # often that every chunk should fully contain at least
                        # one marker.
                        m = re.search(rb"\n\*\*\* [^\t\r\n]+\t([^\t\r\n]+)\t", chunk)
                        if m:
                            try:
                                timestamp_in_chunk = datetime.fromisoformat(m.group(1).decode("ascii"))
                            except (ValueError, UnicodeError):
                                pass
                            else:
                                if timestamp_in_chunk < since:
                                    break

            except OSError:
                continue

            chunks.reverse()
            # First line may be damaged because the first chunk we took
            # might contain only a part of it. But we never need the
            # first line anyway: if it's from start of file, it should
            # be blank because the "LOGGING BEGINS" thing has blank
            # lines in front of it.
            lines = b"".join(chunks).decode("utf-8", errors="replace").splitlines()[1:]

            # Due to file name sanitization duplicates, the same log file can
            # contain logs from e.g. DMs with multiple different users.
            #
            # Figure out what the messages the start of the log are from.
            is_relevant = False
            for line in lines:
                if line.startswith("***"):
                    is_relevant = (line.split("\t")[2:4] == [server_name, channel_or_nick])
                    break

            for line in lines:
                if not line:
                    continue
                try:
                    if line.startswith("***"):
                        is_relevant = (line.split("\t")[2:4] == [server_name, channel_or_nick])
                    else:
                        if is_relevant:
                            # It's important to specify maxsplit here because
                            # the message may contain tab characters. (I think?)
                            timestamp, sender, message = line.split("\t", maxsplit=2)
                            results.append((
                                datetime.fromisoformat(timestamp),
                                None if sender == "*" else sender,
                                message,
                            ))
                except ValueError:
                    print(f"IRC log line doesn't seem to be from mantaray: {line!r}")

            # Might help with memory usage... probably doesn't matter
            chunks.clear()
            lines.clear()

        # Combine results from all files and sort by timestamp.
        # Works because timestamp is the first field.
        results.sort()
        return results
