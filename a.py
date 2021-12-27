import time
import sys
import subprocess
import shutil
import tkinter
import tempfile
from pathlib import Path

from mantaray import gui, config
from mantaray.views import ServerView


def wait_until(root_window, condition, *, timeout=5):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        root_window.update()
        if condition():
            return
    raise RuntimeError("timed out waiting")


class _Hircd:
    def __init__(self, hircd_repo):
        self._hircd_repo = hircd_repo
        self.process = None

    def start(self):
        self.process = subprocess.Popen(
            [sys.executable, "hircd.py", "--foreground", "--verbose", "--log-stdout"],
            stderr=subprocess.PIPE,
            cwd=self._hircd_repo,
        )

        # Wait for it to start
        for line in self.process.stderr:
            assert b"ERROR" not in line
            if line.startswith(b"[INFO] Starting hircd on "):
                port = int(line.split(b":")[-1])
                return port
        raise RuntimeError

    def stop(self):
        self.process.kill()

        output = self.process.stderr.read()
        if b"ERROR" in output:
            print(output.decode("utf-8", errors="replace"))
            raise RuntimeError


def test_part_last_channel():
    print(50*"A", flush=True)
    root_window = tkinter.Tk()
    print(50*"B", flush=True)
    try:
        print(50*"C", flush=True)
        clone_url = "https://github.com/fboender/hircd"
        hircd_repo = Path(__file__).absolute().parent / "hircd"
        if not hircd_repo.is_dir():
            subprocess.check_call(["git", "clone", clone_url], cwd=hircd_repo.parent)

        correct_commit = "d09d4f9a11b99f49a1606477ab9d4dadcee35e7c"
        actual_commit = (
            subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=hircd_repo)
            .strip()
            .decode("ascii")
        )
        if actual_commit != correct_commit:
            subprocess.check_call(["git", "fetch", clone_url], cwd=hircd_repo)
            subprocess.check_call(["git", "checkout", correct_commit], cwd=hircd_repo)

        hircd = _Hircd(hircd_repo)
        hircd.start()
        print(50*"D", flush=True)
        alice = gui.IrcWidget(
            root_window,
            config.load_from_file(Path("alice")),
            Path(tempfile.mkdtemp(prefix="mantaray-tests-")),
        )
        print(50*"E", flush=True)
        alice.pack(fill="both", expand=True)
        print(50*"F", flush=True)
        wait_until(root_window, lambda: "The topic of #autojoin is" in alice.text())
    finally:
        print(50*"W", flush=True)
        root_window.destroy()
        print(50*"X", flush=True)


test_part_last_channel()
