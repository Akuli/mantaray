import functools
import tkinter
from getpass import getuser

from . import gui, config


def update_title(
    root: tkinter.Tk, irc_widget: gui.IrcWidget, junk_event: object = None
) -> None:
    title = "IRC: %s" % irc_widget.core.host
    number = irc_widget.not_seen_count()
    if number != 0:
        title = "(%d) %s" % (number, title)
    root.title(title)


# TODO: current_channel_like_notify and mark_seen()
def main() -> None:
    # tkinter must have one global window, but server configging creates dialog
    # solution: hide root window temporarily
    root = tkinter.Tk()
    root.withdraw()

    server_config = config.show_server_config_dialog(
        transient_to=None,
        initial_config={
            "host": "irc.libera.chat",
            "port": 6697,
            "nick": getuser(),
            "username": getuser(),
            "realname": getuser(),
            "join_channels": ["##learnpython"],
        },
    )
    if server_config is None:
        return

    irc_widget = gui.IrcWidget(root, server_config, root.destroy)
    irc_widget.pack(fill="both", expand=True)
    root.bind("<FocusIn>", (lambda junk_event: irc_widget.focus_the_entry()))
    root.protocol("WM_DELETE_WINDOW", irc_widget.part_all_channels_and_quit)

    update_the_title = functools.partial(update_title, root, irc_widget)
    update_the_title()
    irc_widget.bind("<<NotSeenCountChanged>>", update_the_title)

    irc_widget.handle_events()  # doesn't block
    root.deiconify()  # unhide
    root.mainloop()


if __name__ == "__main__":
    main()
