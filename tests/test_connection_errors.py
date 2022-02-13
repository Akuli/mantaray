import re
import sys
import time


def test_clean_connect(alice):
    [server_view] = alice.get_server_views()

    # None of the messages sent during normal connecting should get tagged as error.
    assert (
        "error" in server_view.textwidget.tag_names()
    )  # Fails if tags are renamed in refactoring
    assert not alice.get_server_views()[0].textwidget.tag_ranges("error")


def test_quitting_while_disconnected(alice, irc_server, monkeypatch, wait_until):
    irc_server.process.kill()
    if sys.platform == "win32":
        # error message depends on language
        wait_until(lambda: "Connection error: [WinError 10054] " in alice.text())
    else:
        wait_until(
            lambda: (
                "Connection error: Server closed the connection!" in alice.text()
                or (
                    # This error happens rarely, but it's possible too
                    "Connection error: [Errno 104] Connection reset by peer"
                    in alice.text()
                )
            )
        )
    assert alice.get_current_view().channel_name == "#autojoin"

    start = time.monotonic()
    alice.get_server_views()[0].core.quit(wait=True)
    end = time.monotonic()

    # Typical delays delays can vary:
    #   - Linux: 0.00015sec
    #   - MacOS: 0.0003sec
    #   - Windows: 0.93sec
    #
    # I don't know why windows is so bad, but it really is.
    if sys.platform == "win32":
        assert end - start < 2
    else:
        assert end - start < 0.1


def test_server_dies(alice, bob, irc_server, monkeypatch, wait_until):
    monkeypatch.setattr("mantaray.backend.RECONNECT_SECONDS", 2)
    assert "Connecting to localhost" not in alice.text()

    irc_server.process.kill()
    wait_until(lambda: "Cannot connect (reconnecting in 2sec):" in alice.text())

    lines = alice.text().splitlines()
    assert "Connection error: " in lines[-4]
    assert lines[-3].endswith("Disconnected.")
    assert lines[-2].endswith("Connecting to localhost port 6667...")
    assert "Cannot connect (reconnecting in 2sec):" in lines[-1]
    if sys.platform == "win32":
        # error message depends on language
        assert "[WinError 10061]" in lines[-1]
    else:
        assert lines[-1].endswith("Connection refused")

    irc_server.start()
    for user in [alice, bob]:
        wait_until(
            lambda: (
                "\nConnecting to localhost port 6667...\nThe topic of #autojoin is: (no topic)\n"
                in re.sub(r".*\t", "", user.text())
            )
        )

    assert alice.get_current_view().userlist.get_nicks() == ("Alice", "Bob")
    assert bob.get_current_view().userlist.get_nicks() == ("Alice", "Bob")


def test_order_bug(alice, mocker, monkeypatch, wait_until):
    server_view = alice.get_server_views()[0]
    server_view.core.apply_config_and_reconnect(server_view.get_current_config())
    wait_until(
        lambda: "Disconnected." in alice.text() and "Connecting to" in alice.text()
    )
    assert alice.text().index("Disconnected.") < alice.text().index("Connecting to")
