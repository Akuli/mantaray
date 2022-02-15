import logging
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

os.environ.setdefault("IRC_SERVER", "mantatail")


# https://github.com/pytest-dev/pytest/issues/8887
@pytest.fixture(scope="function", autouse=True)
def check_no_errors_logged(request):
    if "caplog" in request.fixturenames:
        # Test uses caplog fixture, expects to get logging errors
        yield
    else:
        # Fail test if it logs an error
        errors = []
        handler = logging.Handler()
        handler.setLevel(logging.ERROR)
        handler.emit = errors.append
        logging.getLogger().addHandler(handler)
        yield
        logging.getLogger().removeHandler(handler)
        assert not errors


@pytest.fixture(scope="session")
def root_window():
    root = ThemedTk(theme="black")
    root.geometry("800x500")
    root.report_callback_exception = lambda *args: logging.error(
        "error in tkinter callback", exc_info=args
    )
    yield root
    root.destroy()


# Prevents cyclic dependencies with fixtures. It's weird.
@pytest.fixture
def irc_widgets_dict():
    return {}


@pytest.fixture
def wait_until(root_window, irc_widgets_dict):
    def actually_wait_until(condition, *, timeout=5):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            root_window.update()
            if condition():
                return

        message = "timed out waiting"
        for name, widget in irc_widgets_dict.items():
            try:
                message += f"\n{name}'s text = {widget.text()!r}"
            except Exception:
                message += f"\n{name}'s text = <error>"
        raise RuntimeError(message)

    return actually_wait_until


class _IrcServer:
    def __init__(self):
        self.process = None

    def start(self):
        # Make sure that prints appear right away
        env = dict(os.environ)
        env["PYTHONUNBUFFERED"] = "1"

        if os.environ["IRC_SERVER"] == "mantatail":
            command = [sys.executable, "server.py"]
            working_dir = "tests/MantaTail"
        elif os.environ["IRC_SERVER"] == "hircd":
            command = [
                sys.executable,
                "hircd.py",
                "--foreground",
                "--verbose",
                "--log-stdout",
            ]
            working_dir = "tests/hircd"
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

    output = server.process.stdout.read()

    if os.environ["IRC_SERVER"] == "hircd":
        # A bit of a hack, but I don't care about disconnect errors
        output = (
            output.replace(b"BrokenPipeError:", b"")
            .replace(b"ConnectionAbortedError: [WinError 10053]", b"")
            .replace(b"ConnectionResetError: [Errno 54]", b"")
            .replace(b"[ERROR] :localhost 421 CAP :Unknown command", b"")
        )

    if b"error" in output.lower():
        print(output.decode("utf-8", errors="replace"))
        raise RuntimeError


@pytest.fixture
def alice_and_bob(irc_server, root_window, wait_until, mocker, irc_widgets_dict):
    mocker.patch("mantaray.views._show_popup")

    try:
        for name in ["alice", "bob"]:
            users_who_join_before = list(irc_widgets_dict.values())
            irc_widgets_dict[name] = gui.IrcWidget(
                root_window,
                config.load_from_file(Path(name)),
                Path(tempfile.mkdtemp(prefix=f"mantaray-tests-{name}-")),
            )
            irc_widgets_dict[name].pack(fill="both", expand=True)
            # Fails sometimes on macos github actions, don't know yet why
            # TODO: still failing with bigger timeout?
            wait_until(
                lambda: "The topic of #autojoin is" in irc_widgets_dict[name].text(),
                timeout=15,
            )

            for user in users_who_join_before:
                wait_until(
                    lambda: f"{name.capitalize()} joined #autojoin" in user.text()
                )

        yield irc_widgets_dict

    finally:
        # If this cleanup doesn't run, we might leave threads running that will disturb other tests
        for irc_widget in irc_widgets_dict.values():
            for server_view in irc_widget.get_server_views():
                server_view.core.quit(wait=True)

            # On windows, we need to wait until log files are closed before removing them
            wait_until(lambda: not irc_widget.winfo_exists())
            shutil.rmtree(irc_widget.log_manager.log_dir)


@pytest.fixture
def alice(alice_and_bob):
    return alice_and_bob["alice"]


@pytest.fixture
def bob(alice_and_bob):
    return alice_and_bob["bob"]
