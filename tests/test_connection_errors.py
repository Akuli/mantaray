import os
import re
import subprocess
import sys
import time

import pytest


@pytest.mark.skipif(
    os.environ["IRC_SERVER"] == "hircd",
    reason="hircd responds to CAP commands with error",
)
def test_clean_connect(alice):
    [server_view] = alice.get_server_views()

    # None of the messages sent during normal connecting should get tagged as error.
    assert (
        "error" in server_view.textwidget.tag_names()
    )  # Fails if tags are renamed in refactoring
    assert not alice.get_server_views()[0].textwidget.tag_ranges("error")


def test_server_doesnt_respond_to_ping(alice, wait_until, monkeypatch):
    # values don't matter much, just has to be small and distinct enough
    monkeypatch.setattr("mantaray.backend.IDLE_BEFORE_PING_SECONDS", 2)
    monkeypatch.setattr("mantaray.backend.PING_TIMEOUT_SECONDS", 1)
    monkeypatch.setattr("mantaray.backend.RECONNECT_SECONDS", 1.5)
    expected_ping_duration = 2 + 1

    # Create a proxy server that discards PING messages.
    # Using a subprocess so it's easy to kill
    proxy_server = subprocess.Popen(
        [
            sys.executable,
            "-c",
            """
import socket, threading

server = socket.create_connection(("localhost", 6667))
client = socket.create_server(("localhost", 12345)).accept()[0]

def server_to_client():
    for line in server.makefile("rb"):
        client.sendall(line)
threading.Thread(target=server_to_client).start()

for line in client.makefile("rb"):
    if not line.startswith(b"PING "):
        server.sendall(line)
            """,
        ]
    )
    time.sleep(0.5)  # Wait for it to start

    try:
        # Modify config to connect to proxy server
        server_view = alice.get_server_views()[0]
        server_view.settings.port = 12345
        server_view.core.reconnect()

        wait_until(
            lambda: alice.text().count("Connecting to localhost port 12345...") == 1
        )
        wait_until(lambda: alice.text().count("The topic of #autojoin is") == 2)

        start_time = time.monotonic()
        wait_until(
            lambda: (
                "Connection error (reconnecting in 1.5sec): Server did not respond to ping in 1 seconds."
                in alice.text()
            )
        )
        end_time = time.monotonic()
    finally:
        proxy_server.kill()

    duration = end_time - start_time
    # difference is about 0.06 on my system
    abs(duration - expected_ping_duration) < 0.5

    wait_until(lambda: alice.text().count("Connecting to localhost port 12345...") == 2)


def test_quitting_while_disconnected(alice, irc_server, monkeypatch, wait_until):
    irc_server.process.kill()
    wait_until(
        lambda: any(
            error_message in alice.text()
            for error_message in [
                # WinError text depends on windows language
                # Connection reset by peer error happens rarely, but it's possible too
                "Connection error (reconnecting in 5sec): [WinError 10054] ",
                "Connection error (reconnecting in 5sec): Server closed the connection!",
                "Connection error (reconnecting in 5sec): [Errno 104] Connection reset by peer",
            ]
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


@pytest.mark.skipif(
    os.environ["IRC_SERVER"] == "hircd",
    reason="hircd responds to CAP commands with error",
)
def test_server_dies(alice, bob, irc_server, monkeypatch, wait_until):
    monkeypatch.setattr("mantaray.backend.RECONNECT_SECONDS", 2)
    assert "Connecting to localhost" not in alice.text()

    irc_server.process.kill()
    wait_until(lambda: "Cannot connect (reconnecting in 2sec):" in alice.text())

    lines = alice.text().splitlines()
    assert "Connection error (reconnecting in 2sec): " in lines[-3]
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
