"""Hidden-Tk regression smoke for the settings visual shell."""
from __future__ import annotations

import tkinter as tk
import os
import sys

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

from config import DEFAULT_CONFIG
from ui.settings import SettingsWindow
from ui.settings_theme import TOKENS


class _Parent:
    def __init__(self, root):
        self.root = root
        self.config = DEFAULT_CONFIG


def main() -> None:
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        print(f"settings-visual-smoke: SKIP ({exc})")
        return
    root.withdraw()
    settings = None
    try:
        settings = SettingsWindow(_Parent(root), DEFAULT_CONFIG, reader=None)
        assert len(settings._tab_buttons) == len(settings._tab_pages) >= 10
        first_key = settings._tab_order[0]
        settings._select_tab(first_key)
        assert settings._tab_buttons[first_key].cget("bg") == TOKENS["accent_soft"]
        assert settings._footer_bar.winfo_children()
        print("settings-visual-smoke: OK")
    finally:
        if settings and settings.window:
            settings.window.destroy()
        root.destroy()


if __name__ == "__main__":
    main()
