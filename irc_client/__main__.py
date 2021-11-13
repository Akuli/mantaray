from __future__ import annotations

import argparse
import functools
import tkinter
from getpass import getuser

from . import gui, config


def update_title(
    root: tkinter.Tk, irc_widget: gui.IrcWidget, junk_event: object = None
) -> None:
    title = f"IRC: {irc_widget.core.host}"
    number = irc_widget.not_seen_count()
    if number != 0:
        title = f"({number}) " + title
    root.title(title)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--no-config", action="store_true", help="do not read or write the config file"
    )
    args = parser.parse_args()

    # tkinter must have one global root window, but server configging creates dialog
    # solution: hide root window temporarily
    root = tkinter.Tk()
    root.withdraw()

    file_config = None if args.no_config else config.load_from_file()
    if file_config is None:
        server_config = config.show_server_config_dialog(
            transient_to=None,
            initial_config={
                "host": "irc.libera.chat",
                "port": 6697,
                "ssl": True,
                "nick": getuser(),
                "username": getuser(),
                "realname": getuser(),
                "joined_channels": ["##learnpython"],
                "extra_notifications": [],
            },
        )
        if server_config is None:
            return
    else:
        # TODO: support multiple servers
        [server_config] = file_config["servers"]

    def on_any_widget_focused(event: tkinter.Event[tkinter.Misc]) -> None:
        if event.widget == root:
            # Focus the entry, even if a different widget is clicked
            # If you click the widget twice, this won't steal the focus second time
            root.after_idle(irc_widget.entry.focus)

    irc_widget = gui.IrcWidget(root, server_config, root.destroy)
    irc_widget.pack(fill="both", expand=True)
    root.bind("<FocusIn>", on_any_widget_focused)
    root.protocol("WM_DELETE_WINDOW", irc_widget.core.quit)

    update_the_title = functools.partial(update_title, root, irc_widget)
    update_the_title()
    irc_widget.bind("<<NotSeenCountChanged>>", update_the_title)

    irc_widget.handle_events()  # doesn't block
    root.deiconify()  # unhide
    root.mainloop()

    if not args.no_config:
        new_config = irc_widget.get_current_config()
        config.save_to_file({"servers": [new_config]})


if __name__ == "__main__":
    main()
