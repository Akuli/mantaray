import re

import pytest

from mantaray.views import ServerView


def _read_file(path):
    string = path.read_text("utf-8")
    string = re.sub(
        r"[A-Z][a-z][a-z] [A-Z][a-z][a-z] [ \d]\d \d\d:\d\d:\d\d \d\d\d\d",
        "<time>",
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
            print(path.read_text("utf-8"))
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

*** LOGGING BEGINS <time>
<time>  *       The topic of #autojoin is: (no topic)
<time>  *       Bob joined #autojoin.
<time>  Alice   Hello
<time>  Bob     Hiii
*** LOGGING ENDS <time>
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

*** LOGGING BEGINS <time>
<time>  *       The topic of #autojoin is: (no topic)
<time>  *       Bob joined #autojoin.
<time>  *       Bob is now known as blabla.
*** LOGGING ENDS <time>
""",
    )
    check_log(
        alice.log_manager.log_dir / "localhost" / "bob.log",
        """

*** LOGGING BEGINS <time>
<time>  Alice   hey
<time>  *       Bob is now known as blabla.
*** LOGGING ENDS <time>
""",
    )
    check_log(
        alice.log_manager.log_dir / "localhost" / "blabla.log",
        """

*** LOGGING BEGINS <time>
<time>  Alice   its ur new nick
*** LOGGING ENDS <time>
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

*** LOGGING BEGINS <time>
<time>  {Bruh}  blah
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

*** LOGGING BEGINS <time>
<time>  {foo    hello 1
""",
    )

    check_log(
        bob.log_manager.log_dir / "localhost" / "_foo(2).log",
        """

*** LOGGING BEGINS <time>
<time>  }foo    hello 2
""",
    )


def test_someone_has_nickname_server(alice, bob, wait_until, check_log):
    alice.entry.insert(0, "/nick server")
    alice.on_enter_pressed()
    wait_until(lambda: "You are now known as server." in alice.text())

    alice.entry.insert(0, "/msg Bob blah")
    alice.on_enter_pressed()
    wait_until(lambda: "blah" in bob.text())

    bob.entry.insert(0, "hello there")
    bob.on_enter_pressed()
    wait_until(lambda: "hello there" in alice.text())

    check_log(
        bob.log_manager.log_dir / "localhost" / "server(2).log",
        """

*** LOGGING BEGINS <time>
<time>  server  blah
<time>  Bob     hello there
""",
    )
