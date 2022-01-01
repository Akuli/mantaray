import re
import sys
import time
from typing import IO
from pathlib import Path


class LogManager:
    def __init__(self, log_dir: Path):
        self.log_dir = log_dir
        self._opened: dict[IO[str], Path] = {}

    def open_log_file(self, server_name: str, channel_or_nick: str) -> IO[str]:
        safe_folder = re.sub("[^A-Za-z0-9-_#]", "_", server_name.lower())
        safe_file = re.sub("[^A-Za-z0-9-_#]", "_", channel_or_nick.lower())

        # Even if someone's nickname is "server", logs shouldn't get mixed.
        # The actual server.log is created first.
        n = 1
        path = self.log_dir / safe_folder / f"{safe_file}.log"
        while path in self._opened.values():
            n += 1
            path = self.log_dir / safe_folder / f"{safe_file}({n}).log"

        path.parent.mkdir(parents=True, exist_ok=True)
        file = path.open("a", encoding="utf-8")
        if sys.platform != "win32":
            path.chmod(0o600)

        print("\n\n*** LOGGING BEGINS", time.asctime(), file=file, flush=True)
        self._opened[file] = path
        return file

    def close_log_file(self, file: IO[str]) -> None:
        print("*** LOGGING ENDS", time.asctime(), file=file, flush=True)
        file.close()
        del self._opened[file]
