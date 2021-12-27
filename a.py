import time
import sys
import subprocess
import tkinter
import tempfile
from pathlib import Path

from mantaray import gui, config


subprocess.check_call(["git", "clone", "https://github.com/fboender/hircd"])

process = subprocess.Popen(
    [sys.executable, "hircd.py", "--foreground", "--verbose", "--log-stdout"],
    stderr=subprocess.PIPE,
    cwd="hircd",
)
for line in process.stderr:
    assert b"ERROR" not in line
    if line.startswith(b"[INFO] Starting hircd on "):
        break

root_window = tkinter.Tk()
alice = gui.IrcWidget(
    root_window,
    config.load_from_file(Path("alice")),
    Path(tempfile.mkdtemp(prefix="mantaray-tests-")),
)
alice.pack(fill="both", expand=True)

end = time.monotonic() + 5
while time.monotonic() < end:
    root_window.update()

print(50*"W", flush=True)
root_window.destroy()
print(50*"X", flush=True)
