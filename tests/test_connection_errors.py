import time


def test_quitting_while_disconnected(alice, hircd, monkeypatch, wait_until):
    hircd.stop()
    wait_until(
        lambda: (
            "Error while receiving: Server closed the connection!"
            in alice.text()
        )
    )
    assert alice.get_current_view().name == "#autojoin"

    start = time.monotonic()
    alice.get_current_view().server_view.core.quit()
    alice.get_current_view().server_view.core.wait_until_stopped()
    end = time.monotonic()
    assert end - start < 0.5  # on my computer, typically 0.08 or so


def test_server_dies(alice, hircd, monkeypatch, wait_until):
    monkeypatch.setattr("irc_client.backend.RECONNECT_SECONDS", 2)

    hircd.stop()
    wait_until(lambda: "Cannot connect (reconnecting in 2sec):" in alice.text())

    lines = alice.text().splitlines()
    assert lines[-3].endswith("Error while receiving: Server closed the connection!")
    assert lines[-2].endswith("Connecting to localhost port 6667...")
    assert "Cannot connect (reconnecting in 2sec):" in lines[-1]
    assert lines[-1].endswith("Connection refused")

    hircd.start()
    wait_until(lambda: alice.text().endswith("Connecting to localhost port 6667...\n"))
    connecting_end = len(alice.text())
    wait_until(lambda: "The topic of #autojoin is: No topic" in alice.text()[connecting_end:])
    assert alice.get_current_view().userlist.get_nicks() == ("Alice", "Bob")
