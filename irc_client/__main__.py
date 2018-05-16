import functools
import tkinter

from . import connectdialog, gui


def update_title(root, irc_widget, junk_event=None):
    title = "IRC: %s" % irc_widget.core.host
    number = irc_widget.not_seen_count()
    if number != 0:
        title = "(%d) %s" % (number, title)
    root.title(title)


# TODO: current_channel_like_notify and mark_seen()
def main():
    # connectdialog wants an existing root window, but i don't want to show it
    root = tkinter.Tk()
    root.withdraw()   # hide it

    # i know, it doesn't look like we pass in the root window
    # but tkinter has 1 global root window
    # if we hadn't created root before, it would be created automatically
    # and then it wouldn't be hidden
    irc_core = connectdialog.run()
    if irc_core is None:    # cancelled
        root.destroy()
        return
    root.deiconify()   # unhide

    irc_widget = gui.IrcWidget(root, irc_core, root.destroy)
    irc_widget.pack(fill='both', expand=True)
    root.bind('<FocusIn>', (lambda junk_event: irc_widget.focus_the_entry()))
    root.protocol('WM_DELETE_WINDOW', irc_widget.part_all_channels_and_quit)

    update_the_title = functools.partial(update_title, root, irc_widget)
    update_the_title()
    irc_widget.bind('<<NotSeenCountChanged>>', update_the_title)

    irc_widget.handle_events()   # doesn't block
    root.mainloop()


if __name__ == '__main__':
    main()
