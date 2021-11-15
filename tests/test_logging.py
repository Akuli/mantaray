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
    #wait_until(lambda: not alice.winfo_exists())
    #alice.get_current_view().server_view.core.wait_until_stopped()
    t = time.time()
    wait_until(lambda: time.time() > t+2)
    assert (alice.log_dir / "localhost" / "#autojoin.log").read_text("ascii") == "lol"
