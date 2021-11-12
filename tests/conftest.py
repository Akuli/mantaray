import time
import tkinter
import sys
import subprocess
from pathlib import Path

from irc_client import gui

import pytest


@pytest.fixture(scope="session")
def root_window():
    root = tkinter.Tk()
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


@pytest.fixture
def hircd():
    hircd_repo = Path(__file__).absolute().parent / "hircd"
    if not hircd_repo.is_dir():
        subprocess.check_call(
            ["git", "clone", "https://github.com/Akuli/hircd"], cwd=hircd_repo.parent
        )

    correct_commit = "d1ab8a40e0921626fef276431ee0dcd4d3e53403"  # on py3 branch
    actual_commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=hircd_repo).strip().decode("ascii")
    if actual_commit != correct_commit:
        print((actual_commit, correct_commit))
        subprocess.check_call(["git", "fetch"], cwd=hircd_repo)
        subprocess.check_call(["git", "checkout", correct_commit], cwd=hircd_repo)

    process = subprocess.Popen(
        [sys.executable, "hircd.py", "--foreground", "--verbose", "--log-stdout"], stderr=subprocess.PIPE, cwd=hircd_repo
    )

    # Wait for hircd to start
    for line in process.stderr:
        assert b"ERROR" not in line
        if line.startswith(b"[INFO] Starting hircd on "):
            port = int(line.split(b":")[-1].strip())
            break

    yield {"host": "localhost", "port": port, "ssl": False}
    process.kill()


@pytest.fixture
def alice(hircd, root_window, wait_until):
    widget = gui.IrcWidget(root_window, {
        **hircd,
        "nick": "Alice",
        "username": "alice",
        "realname": "Alice's real name",
        "joined_channels": ["#autojoin"],
    })
    widget.pack()
    widget.handle_events()
    wait_until(lambda: "#autojoin" in widget.channel_likes)
    yield widget
    widget.part_all_channels_and_quit()
    widget.core.wait_until_stopped()


@pytest.fixture
def bob(hircd, root_window, wait_until):
    widget = gui.IrcWidget(root_window, {
        **hircd,
        "nick": "Bob",
        "username": "bob",
        "realname": "Bob's real name",
        "joined_channels": ["#autojoin"],
    })
    widget.pack()
    widget.handle_events()
    wait_until(lambda: "#autojoin" in widget.channel_likes)
    yield widget
    widget.part_all_channels_and_quit()
    widget.core.wait_until_stopped()
