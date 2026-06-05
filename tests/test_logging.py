import random
import re
import io
import os

import pytest

from mantaray.views import ServerView
from mantaray.logs import _read_file_backwards


@pytest.mark.parametrize(
    "data, expected",
    [
        (b"", []),
        (b"hello", [b"hello"]),
        (b"hello\n", [b"hello"]),
        (b"hello\r\n", [b"hello"]),
        (b"one\r\ntwo\r\nthree\r\n", [b"three", b"two", b"one"]),
        (b"one\ntwo\nthree\n\n", [b"", b"three", b"two", b"one"]),
        (b"\n\n\n", [b"", b"", b""]),
        (b"a" * 200, [b"a" * 200]),
        (b"a" * 200 + b"\n", [b"a" * 200]),
        (
            b"a"*200 + b"\n" + b"b"*200 + b"\n" + b"c"*200 + b"\n",
            [b"c"*200, b"b"*200, b"a"*200],
        ),
    ],
)
def test_backwards_reading(data, expected):
    f = io.BytesIO(data)
    # Smaller chunk size to hopefully catch bugs
    iterator = _read_file_backwards(f, chunk_size=random.randint(1, 100))
    assert list(iterator) == expected


def _read_file(path):
    string = path.read_text("utf-8")
    string = re.sub(
        r"[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]\.[0-9][0-9][0-9][0-9][0-9][0-9]\+[0-9][0-9]:[0-9][0-9]",
        "%timestamp%",
        string,
    )
    string = string.expandtabs()
    return string


@pytest.fixture
def check_log(wait_until):
    def actually_check_log(path, expected_content):
        # Sometimes it takes a while for logging to show up.
        # For example, when sending a message, there's two queues polled every 100ms.
        try:
            wait_until(lambda: _read_file(path) == expected_content)
        except RuntimeError as e:
            print()
            print("-" * 50)
            print(_read_file(path))
            print("-" * 50)
            raise e

    return actually_check_log


def test_basic(alice, bob, wait_until, check_log):
    alice.entry.insert(0, "Hello")
    alice.on_enter_pressed()
    wait_until(lambda: "Hello" in bob.text())

    bob.entry.insert(0, "Hiii")
    bob.on_enter_pressed()
    wait_until(lambda: "Hiii" in alice.text())

    alice.get_server_views()[0].core.quit()
    wait_until(lambda: not alice.winfo_exists())

    check_log(
        alice.log_manager.log_dir / "localhost" / "#autojoin.log",
        """

*** LOGGING BEGINS %timestamp%
%timestamp% [#autojoin] * The topic of #autojoin is: (no topic)
%timestamp% [#autojoin] * Bob joined #autojoin.
%timestamp% [#autojoin] <Alice> Hello
%timestamp% [#autojoin] <Bob> Hiii
*** LOGGING ENDS %timestamp%
""",
    )


def test_pm_logs(alice, bob, wait_until, check_log):
    alice.entry.insert(0, "/msg Bob hey")
    alice.on_enter_pressed()
    wait_until(lambda: alice.get_current_view().view_name == "Bob")
    wait_until(lambda: bob.get_current_view().view_name == "Alice")
    assert "hey" in bob.text()

    bob.entry.insert(0, "/nick blabla")
    bob.on_enter_pressed()
    wait_until(lambda: "Bob is now known as blabla." in alice.text())

    alice.entry.insert(0, "its ur new nick")
    alice.on_enter_pressed()
    wait_until(lambda: "its ur new nick" in alice.text())
    wait_until(lambda: "its ur new nick" in bob.text())

    alice.get_server_views()[0].core.quit()
    wait_until(lambda: not alice.winfo_exists())

    check_log(
        alice.log_manager.log_dir / "localhost" / "#autojoin.log",
        """

*** LOGGING BEGINS %timestamp%
%timestamp% [#autojoin] * The topic of #autojoin is: (no topic)
%timestamp% [#autojoin] * Bob joined #autojoin.
%timestamp% [#autojoin] * Bob is now known as blabla.
*** LOGGING ENDS %timestamp%
""",
    )
    check_log(
        alice.log_manager.log_dir / "localhost" / "bob.log",
        """

*** LOGGING BEGINS %timestamp%
%timestamp% [Bob] <Alice> hey
%timestamp% [Bob] * Bob is now known as blabla.
*** LOGGING ENDS %timestamp%
""",
    )
    check_log(
        alice.log_manager.log_dir / "localhost" / "blabla.log",
        """

*** LOGGING BEGINS %timestamp%
%timestamp% [blabla] <Alice> its ur new nick
*** LOGGING ENDS %timestamp%
""",
    )


def test_funny_filenames(alice, bob, wait_until, check_log):
    alice.entry.insert(0, "/nick {Bruh}")
    alice.on_enter_pressed()
    wait_until(lambda: "You are now known as {Bruh}." in alice.text())
    alice.entry.insert(0, "/msg Bob blah")
    alice.on_enter_pressed()
    wait_until(lambda: "blah" in bob.text())

    check_log(
        bob.log_manager.log_dir / "localhost" / "_bruh_.log",
        """

*** LOGGING BEGINS %timestamp%
%timestamp% [{Bruh}] <{Bruh}> blah
""",
    )


def test_same_log_file_name(alice, bob, wait_until, check_log):
    # Prevent Bob from noticing nick change, to make Alice appear as two different users.
    # Ideally there would be a way for tests to have 3 different people talking with each other
    alice.entry.insert(0, "/part #autojoin")
    alice.on_enter_pressed()
    wait_until(lambda: isinstance(alice.get_current_view(), ServerView))

    alice.entry.insert(0, "/nick {foo")
    alice.on_enter_pressed()
    wait_until(lambda: "You are now known as {foo." in alice.text())
    alice.entry.insert(0, "/msg Bob hello 1")
    alice.on_enter_pressed()
    wait_until(lambda: "hello 1" in bob.text())

    alice.entry.insert(0, "/nick }foo")
    alice.on_enter_pressed()
    wait_until(lambda: "You are now known as }foo." in alice.text())
    alice.entry.insert(0, "/msg Bob hello 2")
    alice.on_enter_pressed()
    wait_until(lambda: "hello 2" in bob.text())

    check_log(
        bob.log_manager.log_dir / "localhost" / "_foo.log",
        """

*** LOGGING BEGINS %timestamp%
%timestamp% [{foo] <{foo> hello 1
%timestamp% [}foo] <}foo> hello 2
""",
    )


def test_someone_has_nickname_server(alice, bob, wait_until):
    alice.entry.insert(0, "/nick server")
    alice.on_enter_pressed()
    wait_until(lambda: "You are now known as server." in alice.text())

    alice.entry.insert(0, "/msg Bob blah")
    alice.on_enter_pressed()
    wait_until(lambda: "blah" in bob.text())

    bob.entry.insert(0, "hello there")
    bob.on_enter_pressed()
    wait_until(lambda: "hello there" in alice.text())

    # This is special-cased because server.log also contains all the spam that
    # the IRC server happens to say.
    bob_server_log = bob.log_manager.log_dir / "localhost" / "server.log"
    wait_until(lambda: " [server] <server> blah" in bob_server_log.read_text("utf-8", errors="replace"))
    wait_until(lambda: " [server] <Bob> hello there" in bob_server_log.read_text("utf-8", errors="replace"))
