"""Locating the user data folder from inside the game.

The mod lives in `<user dir>/Mods/[one folder/]tsmp.ts4script`, so the user
dir is the parent of the nearest ancestor called "Mods". This avoids guessing
the localized name of the Documents folder.
"""

import os


def find_user_dir(start=None):
    path = os.path.abspath(start if start is not None else __file__)
    while True:
        parent = os.path.dirname(path)
        if parent == path:
            return None
        if os.path.basename(path).lower() == "mods":
            return parent
        path = parent


def data_dir(start=None):
    """`<user dir>/tsmp`, created on demand. None if the layout is unexpected."""
    user_dir = find_user_dir(start)
    if user_dir is None:
        return None
    path = os.path.join(user_dir, "tsmp")
    if not os.path.isdir(path):
        os.makedirs(path)
    return path
