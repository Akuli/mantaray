import os
import subprocess
import sys
import time

from pathlib import Path

from mantaray import backend, config
from mantaray.irc_logger import build_arg_parser, normalize_log_dir, parse_channel_list


def test_parse_channel_list_splits_commas_and_spaces() -> None:
    assert parse_channel_list(["#foo,#bar #baz"]) == ["#foo", "#bar", "#baz"]


def test_build_arg_parser_combines_channel_arguments(tmp_path: Path) -> None:
    parser = build_arg_parser()
    args = parser.parse_args([
        "--server",
        "irc.example.com",
        "--nick",
        "botnick",
        "--username",
        "botuser",
        "--channels",
        "#foo,#bar",
    ])
    assert args.server == "irc.example.com"
    assert args.nick == "botnick"
    assert args.username == "botuser"
    assert args.channels_str == "#foo,#bar"


def test_normalize_log_dir_rejects_outside_cwd(tmp_path: Path) -> None:
    cwd = Path.cwd()
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    if outside_dir.resolve().is_relative_to(cwd.resolve()):
        raise RuntimeError("Test fixture must be outside current working directory")
    try:
        normalize_log_dir(str(outside_dir))
    except Exception as exc:
        assert "must be under current working directory" in str(exc)
    else:
        raise AssertionError("Expected normalize_log_dir to fail for outside directory")


def test_logger_records_channel_messages_from_real_irc_server(
    irc_server, tmp_path: Path
) -> None:
    sender_settings = config.ServerSettings(
        dict_from_file={
            "host": "localhost",
            "port": 6667,
            "ssl": False,
            "nick": "senderbot",
            "username": "senderbot",
            "realname": "senderbot",
            "joined_channels": [],
        }
    )

    log_dir = Path("logs")
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "mantaray.irc_logger",
            "--server",
            "localhost",
            "--port",
            "6667",
            "--no-ssl",
            "--nick",
            "loggerbot",
            "--username",
            "loggerbot",
            "--realname",
            "loggerbot",
            "--channel",
            "#autojoin",
            "--log-dir",
            str(log_dir),
        ],
        cwd=tmp_path,
        env=dict(os.environ, PYTHONPATH=str(Path.cwd())),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    sender = backend.IrcCore(sender_settings, verbose=False)

    sender_connected = False
    sender_joined = False
    message_sent = False
    channel_log = tmp_path / log_dir / "localhost" / "autojoin.log"

    try:
        start = time.monotonic()
        while time.monotonic() - start < 10:
            sender.run_one_step()
            for event in sender.get_events():
                if (
                    isinstance(event, backend.MessageFromServer)
                    and event.command == "CAP"
                    and len(event.args) >= 2
                    and event.args[1] == "ACK"
                ):
                    sender.send("CAP END")
                elif isinstance(event, backend.MessageFromServer) and event.command == "001":
                    sender_connected = True
                    sender.send("JOIN #autojoin")
                elif (
                    sender_connected
                    and not sender_joined
                    and isinstance(event, backend.MessageFromUser)
                    and event.command == "JOIN"
                    and event.sender_nick == "senderbot"
                ):
                    sender_joined = True
                    sender.send_privmsg("#autojoin", "hello logger")
                    message_sent = True

            if message_sent and channel_log.exists():
                content = channel_log.read_text("utf-8")
                if "hello logger" in content:
                    return
            time.sleep(0.05)

        raise AssertionError("Logger did not record the channel message")
    finally:
        sender.quit(wait=True)
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        if process.stdout is not None:
            output = process.stdout.read()
            if output:
                print("logger output:\n", output)
