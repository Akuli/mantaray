import time
import sys
import subprocess
import tkinter
import tempfile
from pathlib import Path

from mantaray import gui, config


def wait_until(root_window, condition, *, timeout=5):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        root_window.update()
        if condition():
            return
    raise RuntimeError("timed out waiting")


print(50*"A", flush=True)
root_window = tkinter.Tk()
try:
    print(50*"C", flush=True)
    clone_url = "https://github.com/fboender/hircd"
    hircd_repo = Path(__file__).absolute().parent / "hircd"
    subprocess.check_call(["git", "clone", clone_url], cwd=hircd_repo.parent)
    process = subprocess.Popen(
        [sys.executable, "hircd.py", "--foreground", "--verbose", "--log-stdout"],
        stderr=subprocess.PIPE,
        cwd=hircd_repo,
    )
    for line in process.stderr:
        assert b"ERROR" not in line
        if line.startswith(b"[INFO] Starting hircd on "):
            break

    print(50*"D", flush=True)
    alice = gui.IrcWidget(
        root_window,
        config.load_from_file(Path("alice")),
        Path(tempfile.mkdtemp(prefix="mantaray-tests-")),
    )
    print(50*"E", flush=True)
    alice.pack(fill="both", expand=True)
    print(50*"F", flush=True)
    while True:
        root_window.update()
finally:
    print(50*"W", flush=True)
    root_window.destroy()
    print(50*"X", flush=True)
