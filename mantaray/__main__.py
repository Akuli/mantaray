from __future__ import annotations

import argparse
import functools
import sys
import tkinter
import traceback
from pathlib import Path

from . import gui, config

try:
    import appdirs
    from ttkthemes import ThemedTk
except ImportError:
    traceback.print_exc()
    print()
    print("You need to create a venv and install the dependencies into it with pip.")
    print("See README.md for instructions.")
    sys.exit(1)


def update_title(
    root: tkinter.Tk, irc_widget: gui.IrcWidget, junk_event: object = None
) -> None:
    number = irc_widget.not_seen_count()
    root.title("Mantaray" if number == 0 else f"({number}) Mantaray")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--no-config", action="store_true", help="do not read or write the config file"
    )
    args = parser.parse_args()

    # tkinter must have one global root window, but server configging creates dialog
    # solution: hide root window temporarily
    root = ThemedTk(theme="black")
    root.withdraw()

    config_dir = Path(appdirs.user_config_dir("mantaray", "Akuli"))
    if args.no_config:
        file_config = None
    else:
        legacy_config_dir = Path(appdirs.user_config_dir("irc-client", "Akuli"))
        if legacy_config_dir.exists() and not config_dir.exists():
            print("Renaming:", legacy_config_dir, "-->", config_dir)
            legacy_config_dir.rename(config_dir)
        file_config = config.load_from_file(config_dir)

    if file_config is None:
        server_config = config.show_connection_settings_dialog(
            transient_to=None, initial_config=None
        )
        if server_config is None:
            return
        default_family, default_size = config.get_default_fixed_font()
        file_config = {
            "servers": [server_config],
            "font_family": default_family,
            "font_size": default_size,
        }

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
    if sys.platform == "darwin":
        root.bind("<Command-plus>", irc_widget.bigger_font_size)
        root.bind("<Command-minus>", irc_widget.smaller_font_size)
    else:
        root.bind("<Control-plus>", irc_widget.bigger_font_size)
        root.bind("<Control-minus>", irc_widget.smaller_font_size)
    root.bind("<FocusIn>", on_any_widget_focused)
    root.protocol("WM_DELETE_WINDOW", save_config_and_quit_all_servers)

    update_the_title = functools.partial(update_title, root, irc_widget)
    update_the_title()
    irc_widget.bind("<<NotSeenCountChanged>>", update_the_title)

    root.deiconify()  # unhide
    root.mainloop()


if __name__ == "__main__":
    main()
