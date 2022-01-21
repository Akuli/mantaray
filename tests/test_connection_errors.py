import re
import sys
import time

import pytest


@pytest.mark.skipif(
    sys.platform == "win32", reason="fails github actions and I don't know why"
)
def test_quitting_while_disconnected(alice, irc_server, monkeypatch, wait_until):
    irc_server.process.kill()
    if sys.platform == "win32":
        # error message depends on language
        wait_until(
            lambda: "Error while receiving: [WinError 10054] " in alice.text()
        )
    else:
        wait_until(
            lambda: "Error while receiving: Server closed the connection!" in alice.text()
        )
    assert alice.get_current_view().channel_name == "#autojoin"

    start = time.monotonic()
    alice.get_server_views()[0].core.quit()
    alice.get_server_views()[0].core.wait_for_threads_to_stop()
    end = time.monotonic()
    assert end - start < 0.5  # on my computer, typically 0.08 or so


def test_server_dies(alice, irc_server, monkeypatch, wait_until):
    monkeypatch.setattr("mantaray.backend.RECONNECT_SECONDS", 2)
    assert "Connecting to localhost" not in alice.text()

    irc_server.process.kill()
    wait_until(lambda: "Cannot connect (reconnecting in 2sec):" in alice.text())

    lines = alice.text().splitlines()
    if sys.platform == "win32":
        # error message depends on language
        assert "Error while receiving: [WinError 10054] " in lines[-4]
    else:
        assert lines[-4].endswith("Error while receiving: Server closed the connection!")
    assert lines[-3].endswith("Disconnected.")
    assert lines[-2].endswith("Connecting to localhost port 6667...")
    assert "Cannot connect (reconnecting in 2sec):" in lines[-1]
    if sys.platform == "win32":
        # error message depends on language
        assert "[WinError 10061]" in lines[-1]
    else:
        assert lines[-1].endswith("Connection refused")

    irc_server.start()
    wait_until(
        lambda: (
            "\nConnecting to localhost port 6667...\nThe topic of #autojoin is: (no topic)\n"
            in re.sub(r".*\t", "", alice.text())
        )
    )
    assert alice.get_current_view().userlist.get_nicks() == ("Alice", "Bob")


def test_order_bug(alice, mocker, monkeypatch, wait_until):
    server_view = alice.get_server_views()[0]
    server_view.core.apply_config_and_reconnect(server_view.get_current_config())
    wait_until(
        lambda: "Disconnected." in alice.text() and "Connecting to" in alice.text()
    )
    assert alice.text().index("Disconnected.") < alice.text().index("Connecting to")
