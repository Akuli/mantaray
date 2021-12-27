import time
import tkinter
import tempfile
from pathlib import Path


root_window = tkinter.Tk()
alice = gui.IrcWidget(
    root_window,
    {
        "servers": [
            {
                "host": "localhost",
                "port": 6667,
                "ssl": False,
                "nick": "Alice",
                "username": "Alice",
                "realname": "Alice's real name",
                "joined_channels": ["#autojoin"],
                "password": None,
                "extra_notifications": [],
                "join_leave_hiding": {"show_by_default": True, "exception_nicks": []},
            }
        ],
        "font_family": "monospace",
        "font_size": 10,
    },
    Path(tempfile.mkdtemp(prefix="mantaray-tests-")),
)
alice.pack(fill="both", expand=True)

end = time.monotonic() + 5
while time.monotonic() < end:
    root_window.update()

print(50 * "W", flush=True)
root_window.destroy()
print(50 * "X", flush=True)
