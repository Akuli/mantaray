import os
import time
import sys
import subprocess
import shlex
import shutil
import tempfile
from pathlib import Path

from mantaray import gui, config

import pytest
from ttkthemes import ThemedTk


@pytest.fixture(scope="session")
def root_window():
    root = ThemedTk(theme="black")
    root.geometry("800x500")
    yield root
    root.destroy()


@pytest.fixture
def wait_until(root_window):
    def actually_wait_until(condition, *, timeout=5):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            root_window.update()
            if condition():
                return
        raise RuntimeError("timed out waiting")

    return actually_wait_until


class _IrcServer:
    def __init__(self, command, working_dir):
        self._command = command
        self._working_dir = working_dir
        self.process = None

    def start(self):
        # Make sure that prints appear right away
        env = dict(os.environ)
        env["PYTHONUNBUFFERED"] = "1"

        self.process = subprocess.Popen(
            self._command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=env,
            cwd=self._working_dir,
        )

        try:
            # Wait for it to start
            for line in self.process.stdout:
                assert b"error" not in line.lower()
                if b"Starting hircd" in line or b"Mantatail running" in line:
                    return
            raise RuntimeError
        except Exception as e:
            self.process.kill()
            raise e

    def stop(self):
        self.process.kill()

        output = self.process.stdout.read()

        # A bit of a hack, but don't care about disconnect errors
        if b"error" in (
            output.replace(b"BrokenPipeError:", b"")
            .replace(b"ConnectionAbortedError: [WinError 10053]", b"")
            .lower()
        ):
            print(output.decode("utf-8", errors="replace"))
            raise RuntimeError


@pytest.fixture
def irc_server():
    if "IRC_SERVER_COMMAND" in os.environ:
        command = shlex.split(
            os.environ["IRC_SERVER_COMMAND"], posix=(sys.platform != "win32")
        )
        working_dir = os.environ.get("IRC_SERVER_WORKING_DIR", ".")
    else:
        command = [
            sys.executable,
            "hircd.py",
            "--foreground",
            "--verbose",
            "--log-stdout",
        ]
        working_dir = "hircd"

    irc_server = _IrcServer(command, working_dir)
    irc_server.start()
    yield irc_server
    irc_server.stop()


@pytest.fixture
def alice_and_bob(irc_server, root_window, wait_until, mocker):
    mocker.patch("mantaray.views._show_popup")

    widgets = {}
    for name in ["alice", "bob"]:
        widgets[name] = gui.IrcWidget(
            root_window,
            config.load_from_file(Path(name)),
            Path(tempfile.mkdtemp(prefix="mantaray-tests-")),
        )
        widgets[name].pack(fill="both", expand=True)
        wait_until(lambda: "The topic of #autojoin is" in widgets[name].text())

    yield widgets

    for irc_widget in widgets.values():
        if irc_widget.winfo_exists():
            for server_view in irc_widget.get_server_views():
                server_view.core.quit()
                server_view.core.wait_for_threads_to_stop()
        # On windows, we need to wait until log files are closed before removing them
        wait_until(lambda: not irc_widget.winfo_exists())
        shutil.rmtree(irc_widget.log_dir)


@pytest.fixture
def alice(alice_and_bob):
    return alice_and_bob["alice"]


@pytest.fixture
def bob(alice_and_bob):
    return alice_and_bob["bob"]
