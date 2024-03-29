import logging
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import tkinter
from pathlib import Path

import pytest
import sv_ttk

from mantaray import config, gui

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
    root = tkinter.Tk()
    sv_ttk.use_dark_theme()
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


@pytest.fixture
def switch_view():
    def actually_switch_view(irc_widget, view):
        if isinstance(view, str):
            view = irc_widget.get_server_views()[0].find_channel(
                view
            ) or irc_widget.get_server_views()[0].find_pm(view)
            assert view is not None

        irc_widget.view_selector.selection_set(view.view_id)
        irc_widget.update()
        assert irc_widget.get_current_view() == view

    return actually_switch_view


def _port_6667_is_in_use() -> bool:
    try:
        socket.create_connection(("localhost", 6667)).close()
    except ConnectionRefusedError:
        return False
    else:
        return True


class _IrcServer:
    def __init__(self, output_file):
        self.process = None
        self._output_file = output_file

    def start(self):
        if _port_6667_is_in_use():
            raise RuntimeError(
                "an IRC server (or something else) is already running on port 6667"
            )

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
            stdout=self._output_file,
            stderr=self._output_file,
            env=env,
            cwd=working_dir,
        )

        # Wait max 5sec for the server to start
        time_limit = time.monotonic() + 5
        while not _port_6667_is_in_use():
            assert time.monotonic() < time_limit


@pytest.fixture
def irc_server():
    with tempfile.TemporaryFile() as output_file:
        server = _IrcServer(output_file)
        is_error = False

        try:
            server.start()
            yield server
        except Exception as e:
            is_error = True
            raise e
        finally:
            if server.process is not None:
                server.process.kill()
                server.process.wait(timeout=5)
            output_file.seek(0)
            output = output_file.read()
            if is_error:
                print("---- IRC server output begins ----")
                print(output.decode("utf-8", errors="replace"))
                print("---- IRC server output ends ----")

    if os.environ["IRC_SERVER"] == "hircd":
        # A bit of a hack, but I don't care about disconnect errors
        output = (
            output.replace(b"BrokenPipeError:", b"")
            .replace(b"ConnectionAbortedError: [WinError 10053]", b"")
            .replace(b"ConnectionResetError: [WinError 10054]", b"")
            .replace(b"ConnectionResetError: [Errno 54]", b"")
            .replace(b"[ERROR] :localhost 421 CAP :Unknown command", b"")
            .replace(b"[ERROR] :localhost 421 WHOIS :Unknown command", b"")
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
            settings = config.Settings(Path(name), read_only=True)
            settings.load()
            irc_widgets_dict[name] = gui.IrcWidget(
                root_window,
                settings,
                Path(tempfile.mkdtemp(prefix=f"mantaray-tests-{name}-")),
            )
            irc_widgets_dict[name].pack(fill="both", expand=True)
            wait_until(
                lambda: "The topic of #autojoin is" in irc_widgets_dict[name].text()
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
