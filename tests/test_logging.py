import re


def _remove_timestamps(string):
    return re.sub(
        r"[A-Z][a-z][a-z] [A-Z][a-z][a-z] \d\d \d\d:\d\d:\d\d \d\d\d\d",
        "<timestamp>",
        string,
    )


def check_log(path, expected_content):
    content = _remove_timestamps(path.read_text("utf-8"))
    assert content == expected_content


def test_basic(alice, bob, wait_until):
    alice.entry.insert("end", "Hello")
    alice.on_enter_pressed()
    wait_until(lambda: "Hello" in bob.text())

    bob.entry.insert("end", "Hiii")
    bob.on_enter_pressed()
    wait_until(lambda: "Hiii" in alice.text())

    alice.entry.insert("end", "/quit")
    alice.on_enter_pressed()
    wait_until(lambda: not alice.winfo_exists())

    check_log(
        alice.log_dir / "localhost" / "#autojoin.log",
        """\
*** LOGGING BEGINS <timestamp>
<timestamp>\t*\tThe topic of #autojoin is: No topic
<timestamp>\t*\tBob joined #autojoin.
<timestamp>\tAlice\tHello
<timestamp>\tBob\tHiii
*** LOGGING ENDS <timestamp>
""",
    )


def test_pm_logs(alice, bob, wait_until):
    alice.entry.insert("end", "/msg Bob hey")
    alice.on_enter_pressed()
    wait_until(lambda: "hey" in bob.text())

    bob.entry.insert("end", "/nick blabla")
    bob.on_enter_pressed()
    wait_until(lambda: "Bob is now known as blabla." in alice.text())

    alice.entry.insert("end", "its ur new nick")
    alice.on_enter_pressed()
    wait_until(lambda: "its ur new nick" in bob.text())

    alice.entry.insert("end", "/quit")
    alice.on_enter_pressed()
    wait_until(lambda: not alice.winfo_exists())

    check_log(
        alice.log_dir / "localhost" / "#autojoin.log",
        """\
*** LOGGING BEGINS <timestamp>
<timestamp>\t*\tThe topic of #autojoin is: No topic
<timestamp>\t*\tBob joined #autojoin.
<timestamp>\t*\tBob is now known as blabla.
*** LOGGING ENDS <timestamp>
""",
    )
    check_log(
        alice.log_dir / "localhost" / "Bob.log",
        """\
*** LOGGING BEGINS <timestamp>
<timestamp>\tAlice\they
<timestamp>\t*\tBob is now known as blabla.
*** LOGGING ENDS <timestamp>
""",
    )
    check_log(
        alice.log_dir / "localhost" / "blabla.log",
        """\
*** LOGGING BEGINS <timestamp>
<timestamp>\tAlice\tits ur new nick
*** LOGGING ENDS <timestamp>
""",
    )
