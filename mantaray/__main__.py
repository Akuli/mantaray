from __future__ import annotations

import argparse
import functools
import sys
import tkinter
import traceback
from functools import partial
from pathlib import Path
from typing import Callable

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
    number = sum(v.notification_count for v in irc_widget.views_by_id.values())
    root.title("Mantaray" if number == 0 else f"({number}) Mantaray")


def main() -> None:
    default_config_dir = Path(appdirs.user_config_dir("mantaray", "Akuli"))
    legacy_config_dir = Path(appdirs.user_config_dir("irc-client", "Akuli"))

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config-dir",
        type=Path,
        default=default_config_dir,
        help=(
            "path to folder containing config.json and logs folder"
            + f" (default: {default_config_dir})"
        ),
    )
    parser.add_argument(
        "--dont-save-config",
        action="store_true",
        help="do not write to config.json in the config dir",
    )
    parser.add_argument(
        "--verbose", action="store_true", help="print everything sent and received, useful for understanding IRC or development"
    )
    args = parser.parse_args()

    if (
        args.config_dir == default_config_dir
        and legacy_config_dir.exists()
        and not default_config_dir.exists()
    ):
        print("Renaming:", legacy_config_dir, "-->", default_config_dir)
        legacy_config_dir.rename(default_config_dir)

    # tkinter must have one global root window, but server configging creates dialog
    # solution: hide root window temporarily
    root = ThemedTk(theme="black")
    root.withdraw()

    file_config = config.load_from_file(args.config_dir)
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
            irc_widget.get_current_view().mark_seen()

            # Focus the entry, even if a different widget is clicked
            # If you click the widget twice, this won't steal the focus second time
            root.after_idle(irc_widget.entry.focus)

    def save_config_and_quit_all_servers() -> None:
        if not args.dont_save_config:
            config.save_to_file(args.config_dir, irc_widget.get_current_config())
        for server_view in irc_widget.get_server_views():
            server_view.core.quit()

    irc_widget = gui.IrcWidget(
        root, file_config, args.config_dir / "logs", args.verbose
    )
    irc_widget.pack(fill="both", expand=True)
    irc_widget.bind("<Destroy>", lambda e: root.after_idle(root.destroy))

    def add_binding(binding: str, callback: Callable[[], None]) -> None:
        def actual_callback(event: object) -> str:
            callback()
            return "break"

        if sys.platform == "darwin":
            binding = binding.format(ControlOrCommand="Command")
        else:
            binding = binding.format(ControlOrCommand="Control")

        # Must be bound on entry, otherwise Ctrl+PageUp runs PageUp code
        root.bind(binding, actual_callback)
        irc_widget.entry.bind(binding, actual_callback)

    # Don't bind to alt+n, some windows users use it for entering characters as "alt codes"
    add_binding("<{ControlOrCommand}-plus>", irc_widget.bigger_font_size)
    add_binding("<{ControlOrCommand}-minus>", irc_widget.smaller_font_size)
    add_binding("<{ControlOrCommand}-Shift-Prior>", irc_widget.move_view_up)
    add_binding("<{ControlOrCommand}-Shift-Next>", irc_widget.move_view_down)
    add_binding("<{ControlOrCommand}-Prior>", irc_widget.select_previous_view)
    add_binding("<{ControlOrCommand}-Next>", irc_widget.select_next_view)
    for n in range(10):
        add_binding(
            "<{ControlOrCommand}-Key-%d>" % n, partial(irc_widget.select_by_number, n)
        )

    root.bind("<FocusIn>", on_any_widget_focused)
    root.protocol("WM_DELETE_WINDOW", save_config_and_quit_all_servers)

    update_the_title = functools.partial(update_title, root, irc_widget)
    update_the_title()
    irc_widget.bind("<<NotificationCountChanged>>", update_the_title)

    root.geometry("800x500")  # TODO: config file
    root.deiconify()  # unhide
    root.mainloop()


if __name__ == "__main__":
    main()
