import re


def _remove_timestamps(string):
    return re.sub(
        r"[A-Z][a-z][a-z] [A-Z][a-z][a-z] \d\d \d\d:\d\d:\d\d \d\d\d\d",
        "<time>",
        string,
    )


def check_log(path, expected_content):
    assert _remove_timestamps(path.read_text("utf-8")).expandtabs() == expected_content


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
*** LOGGING BEGINS <time>
<time>  *       The topic of #autojoin is: No topic
<time>  *       Bob joined #autojoin.
<time>  Alice   Hello
<time>  Bob     Hiii
*** LOGGING ENDS <time>
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
*** LOGGING BEGINS <time>
<time>  *       The topic of #autojoin is: No topic
<time>  *       Bob joined #autojoin.
<time>  *       Bob is now known as blabla.
*** LOGGING ENDS <time>
""",
    )
    check_log(
        alice.log_dir / "localhost" / "Bob.log",
        """\
*** LOGGING BEGINS <time>
<time>  Alice   hey
<time>  *       Bob is now known as blabla.
*** LOGGING ENDS <time>
""",
    )
    check_log(
        alice.log_dir / "localhost" / "blabla.log",
        """\
*** LOGGING BEGINS <time>
<time>  Alice   its ur new nick
*** LOGGING ENDS <time>
""",
    )
