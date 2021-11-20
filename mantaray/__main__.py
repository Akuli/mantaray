from __future__ import annotations

import argparse
import functools
import tkinter
from pathlib import Path

from . import gui, config

import appdirs


def update_title(
    root: tkinter.Tk, irc_widget: gui.IrcWidget, junk_event: object = None
) -> None:
    number = irc_widget.not_seen_count()
    root.title("IRC" if number == 0 else f"({number}) IRC")


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

    config_dir = Path(appdirs.user_config_dir("mantaray", "Akuli"))
    file_config = None if args.no_config else config.load_from_file(config_dir)
    if file_config is None:
        server_config = config.show_connection_settings_dialog(
            transient_to=None, initial_config=None
        )
        if server_config is None:
            return
        file_config = {"servers": [server_config]}

    def on_any_widget_focused(event: tkinter.Event[tkinter.Misc]) -> None:
        if event.widget == root:
            # Focus the entry, even if a different widget is clicked
            # If you click the widget twice, this won't steal the focus second time
            root.after_idle(irc_widget.entry.focus)

    def save_config_and_quit_all_servers() -> None:
        if not args.no_config:
            config.save_to_file(config_dir, irc_widget.get_current_config())

        for server_view in irc_widget.get_server_views():
            server_view.core.quit()

    irc_widget = gui.IrcWidget(root, file_config, config_dir / "logs")
    irc_widget.pack(fill="both", expand=True)
    irc_widget.bind("<Destroy>", lambda e: root.after_idle(root.destroy))
    root.bind("<FocusIn>", on_any_widget_focused)
    root.protocol("WM_DELETE_WINDOW", save_config_and_quit_all_servers)

    update_the_title = functools.partial(update_title, root, irc_widget)
    update_the_title()
    irc_widget.bind("<<NotSeenCountChanged>>", update_the_title)

    root.deiconify()  # unhide
    root.mainloop()


if __name__ == "__main__":
    main()
