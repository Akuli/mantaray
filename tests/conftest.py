import os
import time
import sys
import subprocess
import shutil
import tempfile
from pathlib import Path

from mantaray import gui, config

import pytest
from ttkthemes import ThemedTk

os.environ.setdefault("IRC_SERVER", "hircd")


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
    def __init__(self):
        self.process = None

    def start(self):
        # Make sure that prints appear right away
        env = dict(os.environ)
        env["PYTHONUNBUFFERED"] = "1"

        if os.environ["IRC_SERVER"] == "mantatail":
            command = [sys.executable, "mantatail.py"]
            working_dir = "mantatail"
        elif os.environ["IRC_SERVER"] == "hircd":
            command = [
                sys.executable,
                "hircd.py",
                "--foreground",
                "--verbose",
                "--log-stdout",
            ]
            working_dir = "hircd"
        else:
            raise RuntimeError(
                f"IRC_SERVER is set to unexpected value '{os.environ['IRC_SERVER']}'"
                f" (should be 'mantatail' or 'hircd')"
            )

        # Try to fail with a nicer error message if someone forgot to init submodules
        if not os.listdir(working_dir):
            with open(".gitmodules") as file:
                for line in file:
                    if line.strip() == "path = " + working_dir:
                        raise RuntimeError(
                            f"'{working_dir}' not found."
                            f" Please run 'git submodule update --init' and try again."
                        )

        # Ensure there is not a currently running process
        assert self.process is None or self.process.poll() is not None

        self.process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=env,
            cwd=working_dir,
        )

        # Wait for it to start
        for line in self.process.stdout:
            assert b"error" not in line.lower()
            if b"Starting hircd" in line or b"Mantatail running" in line:
                break
        else:
            raise RuntimeError


@pytest.fixture
def irc_server():
    server = _IrcServer()
    try:
        server.start()
        yield server
    finally:
        if server.process is not None:
            server.process.kill()

    # A bit of a hack, but I don't care about disconnect errors
    # TODO: .replace() still needed?
    output = (
        server.process.stdout.read()
        .replace(b"BrokenPipeError:", b"")
        .replace(b"ConnectionAbortedError: [WinError 10053]", b"")
        .lower()
    )
    if b"error" in output:
        print(output.decode("utf-8", errors="replace"))
        raise RuntimeError


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
        wait_until(lambda: "The topic of #autojoin is" in widgets[name].text(), timeout=10)

    yield widgets

    for irc_widget in widgets.values():
        for server_view in irc_widget.get_server_views():
            server_view.core.quit()
            server_view.core.wait_for_threads_to_stop()
        # On windows, we need to wait until log files are closed before removing them
        wait_until(lambda: not irc_widget.winfo_exists())
        shutil.rmtree(irc_widget.log_manager.log_dir)


@pytest.fixture
def alice(alice_and_bob):
    return alice_and_bob["alice"]


@pytest.fixture
def bob(alice_and_bob):
    return alice_and_bob["bob"]
