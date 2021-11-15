import re
import time


def _remove_timestamps(s):
    return re.sub(
        r"[A-Z][a-z][a-z] [A-Z][a-z][a-z] \d\d \d\d:\d\d:\d\d \d\d\d\d",
        "<timestamp>",
        s,
    )


def test_basic(alice, bob, wait_until):
    alice.entry.insert("end", "Hello")
    alice.on_enter_pressed()
    wait_until(lambda: "Hello" in bob.text())

    bob.entry.insert("end", "Hiii")
    bob.on_enter_pressed()
    wait_until(lambda: "Hiii" in alice.text())

    alice.get_current_view().server_view.core.quit()
    wait_until(lambda: not alice.winfo_exists())

    # TODO: get rid of hex codes
    expected_log = """\
*** LOGGING BEGINS <timestamp>
<timestamp>\t*\tThe topic of #autojoin is: No topic
<timestamp>\t*\t\x02\x035Bob\x0f joined #autojoin.
<timestamp>\tAlice\tHello
<timestamp>\tBob\tHiii
*** LOGGING ENDS <timestamp>
"""
    actual_log = (alice.log_dir / "localhost" / "#autojoin.log").read_text("ascii")
    assert _remove_timestamps(actual_log) == expected_log
