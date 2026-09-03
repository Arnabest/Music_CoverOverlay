"""Visual tokens and ttk theme helpers for the QQMusicOverlayNG settings UI.

The module deliberately owns presentation only: it does not read or write
overlay configuration, invoke callbacks, or touch rendering state.
"""
from __future__ import annotations

from tkinter import ttk


TOKENS = {
    "bg_base": "#0f1117",
    "bg_nav": "#151823",
    "bg_panel": "#1a1d2a",
    "bg_surface": "#232735",
    "bg_hover": "#2b3040",
    "border": "#30384a",
    "text": "#e8eaf0",
    "muted": "#9499ad",
    "hint": "#6f7488",
    "accent": "#4a8ce5",
    "accent_hover": "#5c9eff",
    "accent_soft": "#2a3a52",
    "success": "#50c850",
    "warning": "#dcaa3c",
    "danger": "#dc5050",
}


def apply_ttk_theme() -> None:
    """Install the settings-only ttk styles without changing widget behavior."""
    style = ttk.Style()
    style.theme_use("clam")
    # Most existing settings controls use ttk's unqualified style names.
    # Theme those names too; named styles alone leave the parameter pane in
    # the platform's light default palette.
    style.configure("TFrame", background=TOKENS["bg_panel"])
    style.configure("TLabel", background=TOKENS["bg_panel"], foreground=TOKENS["text"])
    style.configure("TButton", background=TOKENS["bg_surface"], foreground=TOKENS["text"], borderwidth=0, padding=(10, 5))
    style.map("TButton", background=[("active", TOKENS["bg_hover"]), ("pressed", TOKENS["bg_base"])])
    style.configure("TCheckbutton", background=TOKENS["bg_panel"], foreground=TOKENS["text"])
    style.map(
        "TCheckbutton",
        background=[("active", TOKENS["bg_panel"]), ("pressed", TOKENS["bg_panel"])],
        foreground=[("active", TOKENS["text"]), ("disabled", TOKENS["hint"])],
    )
    style.configure("TCombobox", fieldbackground=TOKENS["bg_surface"], background=TOKENS["bg_surface"], foreground=TOKENS["text"], arrowcolor=TOKENS["muted"])
    style.map("TCombobox", fieldbackground=[("readonly", TOKENS["bg_surface"])], foreground=[("readonly", TOKENS["text"])])
    style.configure("TEntry", fieldbackground=TOKENS["bg_surface"], foreground=TOKENS["text"])
    style.configure("TScale", background=TOKENS["bg_panel"], troughcolor=TOKENS["bg_surface"])
    style.configure("TLabelframe", background=TOKENS["bg_panel"], foreground=TOKENS["muted"], borderwidth=1, relief="flat")
    style.configure("TLabelframe.Label", background=TOKENS["bg_panel"], foreground=TOKENS["muted"])
    style.configure("Vertical.TScrollbar", background=TOKENS["bg_surface"], troughcolor=TOKENS["bg_base"], borderwidth=0, arrowcolor=TOKENS["muted"])
    style.configure("Settings.TFrame", background=TOKENS["bg_panel"])
    style.configure("Settings.TLabel", background=TOKENS["bg_panel"], foreground=TOKENS["text"])
    style.configure(
        "Settings.TButton",
        background=TOKENS["bg_surface"], foreground=TOKENS["text"],
        borderwidth=0, padding=(12, 6), relief="flat",
    )
    style.map(
        "Settings.TButton",
        background=[("active", TOKENS["bg_hover"]), ("pressed", TOKENS["bg_base"])],
        foreground=[("disabled", TOKENS["hint"])],
    )
    style.configure(
        "Accent.Settings.TButton",
        background=TOKENS["accent"], foreground="#ffffff", borderwidth=0,
        padding=(12, 6), relief="flat",
    )
    style.map("Accent.Settings.TButton", background=[("active", TOKENS["accent_hover"]), ("pressed", "#3a78d4")])
    style.configure("Settings.TLabelframe", background=TOKENS["bg_panel"], foreground=TOKENS["muted"], borderwidth=1, relief="flat")
    style.configure("Settings.TLabelframe.Label", background=TOKENS["bg_panel"], foreground=TOKENS["muted"])
    style.configure("Settings.Vertical.TScrollbar", background=TOKENS["bg_surface"], troughcolor=TOKENS["bg_base"], borderwidth=0, arrowcolor=TOKENS["muted"])
