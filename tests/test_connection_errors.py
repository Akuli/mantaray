import time


def test_quitting_while_disconnected(alice, hircd, monkeypatch, wait_until):
    hircd.stop()
    wait_until(
        lambda: (
            "Error while receiving: Server closed the connection!"
            in alice.find_channel("#autojoin").textwidget.get("1.0", "end")
        )
    )

    start = time.monotonic()
    alice.core.quit()
    alice.core.wait_until_stopped()
    end = time.monotonic()
    assert end - start < 0.5  # on my computer, typically 0.08 or so


def test_server_dies(alice, hircd, monkeypatch, wait_until):
    monkeypatch.setattr("irc_client.backend.RECONNECT_SECONDS", 2)

    def text():
        return alice.find_channel("#autojoin").textwidget.get("1.0", "end")

    hircd.stop()
    wait_until(lambda: "reconnecting in 2sec" in text())

    lines = text().strip().splitlines()
    assert lines[-3].endswith("Error while receiving: Server closed the connection!")
    assert lines[-2].endswith("Connecting to localhost port 6667...")
    assert "Cannot connect (reconnecting in 2sec):" in lines[-1]
    assert lines[-1].endswith("Connection refused")

    hircd.start()
    # TODO: Wait until connection is back, check that Alice notices (#35)
    # TODO: verify that after all that waiting, userlist contains alice and bob in correct order
