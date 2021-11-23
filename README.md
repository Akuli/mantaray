# Mantaray

This is an IRC client written in Python with tkinter and ttk.

![Screenshot](screenshot.png)

Supported features:
- SSL
- SASL authentication
- Multiple servers
- Multiple channels
- Private messages
- `/me`
- Notifications for new messages

You can run Mantaray like this:

    $ sudo apt install libnotify-bin   # skip this on MacOS
    $ git clone https://github.com/Akuli/mantaray
    $ cd mantaray
    $ python3 -m venv env
    $ source env/bin/activate
    $ pip install -r requirements.txt
    $ python3 -m mantaray
    
For Windows users the procedure type these commands in Command Prompt:

    $ git clone https://github.com/Akuli/mantaray
    $ cd mantaray
    $ py -m venv env
    $ env\Scripts\activate
    $ pip install -r requirements.txt
    $ py -m mantaray

For developing, you may want to also run `pip install -r requirements-dev.txt`.
