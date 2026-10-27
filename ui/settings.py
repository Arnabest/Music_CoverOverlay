"""Settings window — per-element tabs, working color pickers, X/Y position sliders.
"""
import ctypes
import os, sys, time
# ★ Ensure project root is on sys.path (needed when running settings.py directly)
_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

import tkinter as tk
from tkinter import ttk, colorchooser, messagebox
from copy import deepcopy
from PIL import Image, ImageTk
from config import (
    list_themes, save_theme, load_theme, delete_theme, rename_theme,
    LAYOUT_PRESETS, apply_preset, save_config,
)
from core.logs import get_logger
from ui.settings_theme import TOKENS, apply_ttk_theme

_set_log = get_logger("settings")

_PAD = {"padx": 3, "pady": 1}
FIELDS = {}

_SETTINGS_BG = TOKENS["bg_base"]
_SETTINGS_NAV = TOKENS["bg_nav"]
_SETTINGS_PANEL = TOKENS["bg_panel"]
_SETTINGS_PANEL_ALT = TOKENS["bg_surface"]
_SETTINGS_BORDER = TOKENS["border"]
_SETTINGS_TEXT = TOKENS["text"]
_SETTINGS_MUTED = TOKENS["muted"]
_SETTINGS_ACCENT = TOKENS["accent"]
_SETTINGS_ACCENT_HOVER = TOKENS["accent_hover"]
_SETTINGS_NAV_TITLES = [
    "窗口", "封面", "标题", "歌手", "歌词", "进度",
    "时间", "动画", "来源", "封面库", "主题", "关于",
]


class SettingsWindow:
    """Settings UI with live preview applied to the overlay."""

    def __init__(self, parent, config: dict, reader,
                 on_apply=None, on_close=None):
        self.parent = parent
        self._w = deepcopy(config)
        self.reader = reader
        self.on_apply = on_apply
        self.on_close = on_close
        self.window: tk.Toplevel | None = None
        self._vars: dict[str, tk.Variable] = {}
        self._color_picker_open = False
        self._create()

    # ================================================================
    # Window creation
    # ================================================================

    def _create(self):
        _master = self.parent.root if hasattr(self.parent, 'root') else None
        self.window = tk.Toplevel(_master)
        # ★ Mark as transient so the window manager destroys it automatically
        # when the master (Tk root) is destroyed, even during tk_popup grabs.
        if _master is not None:
            try:
                self.window.transient(_master)
            except Exception:
                pass
        self.window.title("QQMusic Overlay NG — 设置")
        try:
            _icon = os.path.join(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__))), "assets", "app_icon.ico")
            if os.path.exists(_icon):
                self.window.iconbitmap(_icon)
        except Exception:
            pass
        self.window.overrideredirect(True)
        self.window.geometry("1120x780")
        self.window.minsize(960, 660)
        self.window.configure(bg=_SETTINGS_BG)
        self._apply_soft_window_corners()

        self.window.attributes("-topmost", False)
        # ★ Bring to front on open, then behave as normal window
        self.window.lift()
        self.window.focus_force()
        self.window.protocol("WM_DELETE_WINDOW", self._on_close)

        # Style
        apply_ttk_theme()

        # Shell: left vertical tabs, right detail pane.
        self._tab_pages: dict[str, tk.Widget] = {}
        self._tab_buttons: dict[str, tk.Button] = {}
        self._tab_order: list[str] = []
        self._active_tab_key: str | None = None
        self._nav_build_index = 0

        titlebar = tk.Frame(self.window, bg=_SETTINGS_NAV, height=42, highlightthickness=0)
        titlebar.pack(side="top", fill="x")
        titlebar.pack_propagate(False)
        title_label = tk.Label(titlebar, text="QQMusic Overlay NG  ·  设置", bg=_SETTINGS_NAV,
                               fg=_SETTINGS_TEXT, font=("Microsoft YaHei UI", 10, "bold"), anchor="w")
        title_label.pack(side="left", fill="y", padx=14)
        close_btn = tk.Button(titlebar, text="×", command=self._on_close,
                              bg=_SETTINGS_NAV, fg=_SETTINGS_TEXT, activebackground=TOKENS["danger"],
                              activeforeground="#ffffff", bd=0, width=4, font=("Segoe UI", 15), cursor="hand2")
        close_btn.pack(side="right", fill="y")
        for widget in (titlebar, title_label):
            widget.bind("<ButtonPress-1>", self._start_window_drag)
            widget.bind("<B1-Motion>", self._drag_window)

        shell = tk.Frame(self.window, bg=_SETTINGS_BG, highlightthickness=0)
        shell.grid_columnconfigure(2, weight=1)
        shell.grid_rowconfigure(0, weight=1)
        self._shell = shell

        self._nav = tk.Frame(shell, bg=_SETTINGS_NAV, width=172, highlightthickness=0)
        self._nav.grid(row=0, column=0, sticky="ns")
        self._nav.pack_propagate(False)
        tk.Label(self._nav, text="QQMUSIC\nOVERLAY NG", bg=_SETTINGS_NAV, fg=_SETTINGS_TEXT,
                 font=("Microsoft YaHei UI", 11, "bold"), anchor="w", justify="left"
                 ).pack(fill="x", padx=14, pady=(16, 2))
        tk.Label(self._nav, text="设置中心", bg=_SETTINGS_NAV, fg=_SETTINGS_MUTED,
                 font=("Microsoft YaHei UI", 9), anchor="w"
                 ).pack(fill="x", padx=14, pady=(0, 16))
        # Navigation can be taller than a small work area.  Keep its own
        # scroll region so the bottom pages never disappear behind the footer.
        nav_body = tk.Frame(self._nav, bg=_SETTINGS_NAV, highlightthickness=0)
        nav_body.pack(fill="both", expand=True, padx=(6, 2), pady=(0, 8))
        self._nav_canvas = tk.Canvas(nav_body, width=160, bg=_SETTINGS_NAV, highlightthickness=0,
                                     borderwidth=0, relief="flat")
        nav_scrollbar = ttk.Scrollbar(nav_body, orient="vertical", command=self._nav_canvas.yview,
                                      style="Settings.Vertical.TScrollbar")
        self._nav_buttons_frame = tk.Frame(self._nav_canvas, bg=_SETTINGS_NAV, highlightthickness=0)
        nav_window = self._nav_canvas.create_window((0, 0), window=self._nav_buttons_frame, anchor="nw")
        self._nav_buttons_frame.bind(
            "<Configure>",
            lambda _event: self._nav_canvas.configure(scrollregion=self._nav_canvas.bbox("all")),
        )
        self._nav_canvas.bind(
            "<Configure>",
            lambda event: self._nav_canvas.itemconfigure(nav_window, width=event.width),
        )
        self._nav_canvas.configure(yscrollcommand=nav_scrollbar.set)
        self._nav_canvas.pack(side="left", fill="both", expand=True)
        nav_scrollbar.pack(side="right", fill="y")

        def _scroll_nav(event):
            self._nav_canvas.yview_scroll(int(-event.delta / 120), "units")

        self._nav_canvas.bind("<Enter>", lambda _event: self._nav_canvas.bind_all("<MouseWheel>", _scroll_nav))
        self._nav_canvas.bind("<Leave>", lambda _event: self._nav_canvas.unbind_all("<MouseWheel>"))

        tk.Frame(shell, bg=_SETTINGS_BORDER, width=1, highlightthickness=0
                 ).grid(row=0, column=1, sticky="ns", padx=(2, 10))

        self._content = tk.Frame(shell, bg=_SETTINGS_PANEL, highlightthickness=1,
                                 highlightbackground=_SETTINGS_BORDER,
                                 highlightcolor=_SETTINGS_BORDER)
        self._content.grid(row=0, column=2, sticky="nsew")
        self._content.grid_rowconfigure(0, weight=1)
        self._content.grid_columnconfigure(0, weight=1)

        self._build_tabs()

        # Fixed footer: keep action buttons visible on smaller work areas.
        footer = tk.Frame(self.window, bg=_SETTINGS_BG, highlightthickness=0)
        footer.pack(side="bottom", fill="x", padx=10, pady=(0, 8))
        tk.Frame(footer, bg=_SETTINGS_BORDER, height=1, highlightthickness=0
                 ).pack(fill="x", pady=(0, 6))
        bf = tk.Frame(footer, bg=_SETTINGS_BG, highlightthickness=0)
        bf.pack(fill="x")
        self._footer = footer
        self._footer_bar = bf

        # Status label
        self._st = ttk.Label(bf, text="修改自动预览 · 调整后先点「应用」再「确认」",
                             foreground=_SETTINGS_MUTED, background=_SETTINGS_BG,
                             font=("Microsoft YaHei", 9))
        self._st.pack(side="left", padx=6)

        # Buttons — exact same order and behavior as original
        for text, cmd, style in [("恢复默认", self._reset, "Settings.TButton"),
                                 ("取消", self._on_close, "Settings.TButton"),
                                 ("确认", self._confirm, "Accent.Settings.TButton"),
                                 ("应用", self._apply, "Accent.Settings.TButton")]:
            ttk.Button(bf, text=text, command=cmd, style=style).pack(side="right", padx=3)

        # Pack the fixed footer before the expandable shell.  Packing the
        # shell first can allocate the entire client area and clip Apply /
        # Confirm / Cancel on smaller work areas.
        shell.pack(side="top", fill="both", expand=True, padx=10, pady=(10, 4))
        self._fit_to_workarea(default_width=1120, default_height=780)

    def _apply_soft_window_corners(self) -> None:
        """Prefer native Win11 rounded corners; silently retain the Tk fallback elsewhere."""
        if os.name != "nt" or self.window is None:
            return
        try:
            corner_preference = ctypes.c_int(2)  # DWMWCP_ROUND
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                int(self.window.winfo_id()), 33, ctypes.byref(corner_preference), ctypes.sizeof(corner_preference)
            )
        except Exception:
            pass

    def _start_window_drag(self, event) -> None:
        self._drag_origin = (event.x_root, event.y_root, self.window.winfo_x(), self.window.winfo_y())

    def _drag_window(self, event) -> None:
        origin = getattr(self, "_drag_origin", None)
        if not origin:
            return
        x_root, y_root, window_x, window_y = origin
        self.window.geometry(f"+{window_x + event.x_root - x_root}+{window_y + event.y_root - y_root}")

    def _get_window_workarea(self) -> tuple[int, int, int, int]:
        if self.window is None:
            return (0, 0, 980, 720)
        screen_w = int(self.window.winfo_screenwidth())
        screen_h = int(self.window.winfo_screenheight())
        if os.name != "nt":
            return (0, 0, screen_w, screen_h)
        try:
            class RECT(ctypes.Structure):
                _fields_ = [
                    ("left", ctypes.c_long),
                    ("top", ctypes.c_long),
                    ("right", ctypes.c_long),
                    ("bottom", ctypes.c_long),
                ]

            rect = RECT()
            spi_get_work_area = 48
            ok = ctypes.windll.user32.SystemParametersInfoW(
                spi_get_work_area, 0, ctypes.byref(rect), 0
            )
            if ok:
                return (
                    int(rect.left),
                    int(rect.top),
                    int(rect.right),
                    int(rect.bottom),
                )
        except Exception:
            pass
        return (0, 0, screen_w, screen_h)

    def _fit_to_workarea(self, default_width: int = 980,
                         default_height: int = 720) -> None:
        if self.window is None:
            return
        self.window.update_idletasks()
        left, top, right, bottom = self._get_window_workarea()
        work_w = max(int(right - left), 1)
        work_h = max(int(bottom - top), 1)

        margin_x = 24
        margin_y = 32
        usable_w = max(work_w - margin_x * 2, 1)
        usable_h = max(work_h - margin_y * 2, 1)
        width = max(1, min(default_width, usable_w))
        height = max(1, min(default_height, usable_h))

        self.window.minsize(min(860, width), min(600, height))

        x = left + max((work_w - width) // 2, 0)
        y = top + max((work_h - height) // 2, 0)
        self.window.geometry(f"{width}x{height}+{x}+{y}")
        _set_log.info(
            "settings window fitted to workarea: "
            f"workarea={work_w}x{work_h} window={width}x{height} pos=({x},{y})"
        )

    def _build_tabs(self) -> None:
        """Build all settings pages in the left-nav shell."""
        self._nav_build_index = 0
        self._tab_window()
        self._tab_cover()
        self._tab_title()
        self._tab_artist()
        self._tab_lyrics()
        self._tab_progress()
        self._tab_progress_time()
        self._tab_animation()
        self._tab_sources()
        self._tab_cover_library()
        self._tab_themes()
        self._tab_about()
        if self._active_tab_key not in self._tab_pages:
            self._active_tab_key = self._tab_order[0] if self._tab_order else None
        if self._active_tab_key:
            self._select_tab(self._active_tab_key)

    def _select_tab(self, key: str) -> None:
        """Switch the visible page and update left navigation state."""
        if key not in self._tab_pages:
            return
        self._active_tab_key = key
        for tab_key, page in self._tab_pages.items():
            if tab_key == key:
                page.grid()
            else:
                page.grid_remove()
        self._refresh_nav_buttons()

    def _refresh_nav_buttons(self) -> None:
        for key, btn in self._tab_buttons.items():
            selected = key == self._active_tab_key
            btn.configure(
                bg=TOKENS["accent_soft"] if selected else _SETTINGS_NAV,
                fg="#ffffff" if selected else _SETTINGS_MUTED,
                activebackground=_SETTINGS_ACCENT_HOVER if selected else TOKENS["bg_hover"],
                activeforeground="#ffffff",
                relief="flat",
            )

    def _clear_tab_shell(self) -> None:
        """Remove all generated pages and nav buttons before a rebuild."""
        for child in self._content.winfo_children():
            child.destroy()
        for child in self._nav_buttons_frame.winfo_children():
            child.destroy()
        self._tab_pages.clear()
        self._tab_buttons.clear()
        self._tab_order.clear()
        self._nav_build_index = 0

    # ================================================================
    # Tab: Window
    # ================================================================
    def _tab_window(self):
        sf = self._scroll_frame(" 窗口 ")
        w = self._w.setdefault("window", {})
        bg = w.setdefault("background", {})
        row = 0
        self._slider(sf, row, "窗口透明度", self._kp("window.opacity"),
                     w.get("opacity", 0.85), 0.05, 1.0, 0.05, fmt=".0%"); row += 1
        self._color(sf, row, "背景颜色", self._kp("window.background.color"),
                    bg.get("color", "#1a1a2e")); row += 1
        self._slider(sf, row, "背景不透明度", self._kp("window.background.opacity"),
                     bg.get("opacity", 1.0), 0.0, 1.0, 0.05, fmt=".0%"); row += 1
        self._hint(sf, row, "设为零时背景完全透明，元素边缘可能出现轻微异常"); row += 1
        self._slider(sf, row, "窗口缩放", self._kp("window.scale"),
                     w.get("scale", 1.0), 0.3, 3.0, 0.1); row += 1
        size_cfg = w.setdefault("size", {})
        self._slider(sf, row, "画布宽度", self._kp("window.size.width"),
                     size_cfg.get("width", 400), 200, 1500, 10, fmt=".0f", heavy=True); row += 1
        self._slider(sf, row, "画布高度", self._kp("window.size.height"),
                     size_cfg.get("height", 550), 200, 1500, 10, fmt=".0f", heavy=True); row += 1
        self._check(sf, row, "窗口置顶", self._kp("window.always_on_top"),
                    w.get("always_on_top", True)); row += 1
        self._check(sf, row, "鼠标穿透 ⚠ 开启后无法点击窗口，仅可通过托盘菜单操作",
                    self._kp("window.click_through"),
                    w.get("click_through", False)); row += 1
        # (OBS capture: use Display Capture + crop, or Spout when VS2022 available)

    # ================================================================
    # Tab: Cover
    # ================================================================
    def _tab_cover(self):
        sf = self._scroll_frame(" 封面 ")
        node = self._node("cover")
        row = 0

        # Visibility
        self._check(sf, row, "显示封面", self._nk("cover", "visible"), node.get("visible", True)); row += 1

        # Appearance
        ttk.Label(sf.inner, text="— 外观 —", foreground="#8888cc",
                  background="#1e1e2e").grid(row=row, column=0, columnspan=2, sticky="w", padx=8, pady=(8, 2)); row += 1
        self._slider(sf, row, "封面大小", self._nk("cover", "size"),
                     node.get("size", 180), 50, 400, 10, fmt=".0f"); row += 1
        self._combo(sf, row, "形状", self._nk("cover", "shape"),
                    node.get("shape", "circle"), ["circle", "roundrect", "square"]); row += 1
        self._slider(sf, row, "圆角半径", self._nk("cover", "border_radius"),
                     node.get("border_radius", 20), 0, 100, 5); row += 1
        self._slider(sf, row, "边缘羽化", self._nk("cover", "feather"),
                     node.get("feather", 5), 0, 30, 1); row += 1
        ss_raw = node.get("supersample", "2X")
        if not isinstance(ss_raw, str):
            ss_raw = f"{int(ss_raw)}X"
        self._combo(sf, row, "超采样(封面)", self._nk("cover", "supersample"),
                    ss_raw, ["2X", "4X", "6X"]); row += 1

        # Stroke
        stroke = node.setdefault("stroke", {})
        ttk.Label(sf.inner, text="— 描边 —", foreground="#8888cc",
                  background="#1e1e2e").grid(row=row, column=0, columnspan=2, sticky="w", padx=8, pady=(8, 2)); row += 1
        self._color(sf, row, "描边颜色", self._nk("cover", "stroke.color"),
                    stroke.get("color", "")); row += 1
        self._slider(sf, row, "描边宽度(px)", self._nk("cover", "stroke.width"),
                     int(float(stroke.get("width", 0))), 0, 20, 1, fmt=".0f"); row += 1

        # Position
        ttk.Label(sf.inner, text="— 位置 —", foreground="#8888cc",
                  background="#1e1e2e").grid(row=row, column=0, columnspan=2, sticky="w", padx=8, pady=(8, 2)); row += 1
        self._pos_xy(sf, row, "cover"); row += 2

        # Spin animation
        spin = node.setdefault("spin", {})
        ttk.Label(sf.inner, text="— 旋转动画 —", foreground="#8888cc",
                  background="#1e1e2e").grid(row=row, column=0, columnspan=2, sticky="w", padx=8, pady=(8, 2)); row += 1
        self._check(sf, row, "启用旋转", self._spin("enabled"),
                    spin.get("enabled", True)); row += 1
        self._slider(sf, row, "旋转速度(°/s)", self._spin("speed"),
                     spin.get("speed", 30), 1, 120, 1); row += 1
        self._combo(sf, row, "旋转方向 (cw=顺时针, ccw=逆时针)", self._spin("direction"),
                    spin.get("direction", "cw"), ["cw", "ccw"]); row += 1
        self._slider(sf, row, "旋转过渡时长(s) 静止→旋转", self._spin("spin_duration"),
                     spin.get("spin_duration", spin.get("acceleration", 2.5)),
                     0.3, 8.0, 0.1, fmt=".1f"); row += 1
        self._slider(sf, row, "静止过渡时长(s) 旋转→静止", self._spin("stop_duration"),
                     spin.get("stop_duration", spin.get("acceleration", 2.5) * 0.6 if "acceleration" in spin else 1.5),
                     0.2, 6.0, 0.1, fmt=".1f"); row += 1
        self._check(sf, row, "切歌时等待动画结束",
                    self._spin("wait_spin_on_change"),
                    spin.get("wait_spin_on_change", False)); row += 1
        self._check(sf, row, "暂停时保持旋转",
                    self._spin("pause_spin"),
                    spin.get("pause_spin", True)); row += 1

        # ---- Visual effects ----
        fx = node.setdefault("effects", {})
        ttk.Label(sf.inner, text="— 视觉效果 —", foreground="#8888cc",
                  background="#1e1e2e").grid(row=row, column=0, columnspan=2, sticky="w", padx=8, pady=(12, 2)); row += 1

        cd = fx.setdefault("cd_groove", {"enabled": False, "intensity": 0.5, "frequency": 80})
        self._check(sf, row, "CD沟槽反射 (同心圆环纹理)", self._nk("cover", "effects.cd_groove.enabled"),
                    cd.get("enabled", False)); row += 1
        self._slider(sf, row, "  沟槽强度", self._nk("cover", "effects.cd_groove.intensity"),
                     cd.get("intensity", 0.5), 0.0, 2.0, 0.05, fmt=".2f"); row += 1
        self._slider(sf, row, "  沟槽密度 (线数)", self._nk("cover", "effects.cd_groove.frequency"),
                     cd.get("frequency", 80), 20, 200, 5, fmt=".0f"); row += 1

        mb = fx.setdefault("motion_blur", {"enabled": False, "intensity": 0.5})
        self._check(sf, row, "动态模糊 (旋转切向模糊)", self._nk("cover", "effects.motion_blur.enabled"),
                    mb.get("enabled", False)); row += 1
        self._slider(sf, row, "  模糊强度", self._nk("cover", "effects.motion_blur.intensity"),
                     mb.get("intensity", 0.5), 0.0, 2.0, 0.05, fmt=".2f"); row += 1

        sp = fx.setdefault("specular", {"enabled": False, "intensity": 0.6})
        self._check(sf, row, "镜面高光 (光泽CD表面)", self._nk("cover", "effects.specular.enabled"),
                    sp.get("enabled", False)); row += 1
        self._slider(sf, row, "  高光强度", self._nk("cover", "effects.specular.intensity"),
                     sp.get("intensity", 0.6), 0.0, 2.0, 0.05, fmt=".2f")

    # ================================================================
    # Tab: Title
    # ================================================================
    def _tab_title(self):
        self._text_tab(" 歌名 ", "title")

    # ================================================================
    # Tab: Artist
    # ================================================================
    def _tab_artist(self):
        self._text_tab(" 歌手 ", "artist")

    def _text_tab(self, label: str, eid: str):
        sf = self._scroll_frame(label)
        node = self._node(eid)
        row = 0

        # Visibility
        self._check(sf, row, f"显示{label.strip()}", self._nk(eid, "visible"), node.get("visible", True)); row += 1

        # Font
        ttk.Label(sf.inner, text="— 字体 —", foreground="#8888cc",
                  background="#1e1e2e").grid(row=row, column=0, columnspan=2, sticky="w", padx=8, pady=(8, 2)); row += 1
        self._font_pick(sf, row, "字体", eid, "font_family",
                        node.get("font_family", "Microsoft YaHei")); row += 1
        self._slider(sf, row, "字号", self._nk(eid, "font_size"),
                     node.get("font_size", 14 if eid != "title" else 18), 6, 72, 1, fmt=".0f"); row += 1
        self._check(sf, row, "粗体", self._nk(eid, "font_bold"),
                    node.get("font_bold", eid == "title")); row += 1
        self._color(sf, row, "文字颜色", self._nk(eid, "color"),
                    node.get("color", "#ffffff" if eid == "title" else "#aaccff"))

        # Effects
        ttk.Label(sf.inner, text="— 效果 —", foreground="#8888cc",
                  background="#1e1e2e").grid(row=row + 1, column=0, columnspan=2, sticky="w", padx=8, pady=(8, 2)); row += 2
        # Read outline from flat keys (renderer uses these).  from_config()
        # flattens effects.outline.* → outline_color/outline_width, so the
        # nested path only exists if settings has just written it.
        outline_color_val = node.get("outline_color") or node.get("effects", {}).get("outline", {}).get("color", "")
        outline_width_val = node.get("outline_width", node.get("effects", {}).get("outline", {}).get("width", 0))
        outline_default = "#232323" if eid == "title" else "#000000"
        outline_w_default = 1 if eid == "title" else 0
        if not outline_color_val:
            outline_color_val = outline_default
        if not outline_width_val:
            outline_width_val = outline_w_default
        self._color(sf, row, "描边颜色", self._nk(eid, "outline_color"),
                    outline_color_val); row += 1
        self._slider(sf, row, "描边宽度", self._nk(eid, "outline_width"),
                     outline_width_val, 0, 5, 1); row += 1

        # Max width (text wrapping)
        self._slider(sf, row, "最大宽度", self._nk(eid, "max_width"),
                     node.get("max_width", 350), 50, 800, 10); row += 1

        # Alignment
        self._combo(sf, row, "文本对齐 (左/中/右)", self._nk(eid, "alignment"),
                    node.get("alignment", "center"),
                    ["left", "center", "right"]); row += 1

        # Position
        ttk.Label(sf.inner, text="— 位置 —", foreground="#8888cc",
                  background="#1e1e2e").grid(row=row, column=0, columnspan=2, sticky="w", padx=8, pady=(8, 2)); row += 1
        self._pos_xy(sf, row, eid); row += 2

    # ================================================================
    # Tab: Lyrics
    # ================================================================
    def _tab_lyrics(self):
        sf = self._scroll_frame(" 歌词 ")
        node = self._node("lyrics")
        row = 0

        self._check(sf, row, "显示歌词", self._nk("lyrics", "visible"), node.get("visible", True)); row += 1

        # Lyrics source priority
        _lyrics_src = self._w.get("behavior", {}).get("lyrics_source_priority", "qq_first")
        _lyrics_labels = {
            "qq_first":    "QQ音乐优先（无歌词时网易云回退）",
            "ne_first":    "网易云优先（无歌词时QQ回退）",
            "qq_only":     "仅QQ音乐",
            "ne_only":     "仅网易云音乐",
        }
        self._combo(sf, row, "歌词来源", "behavior.lyrics_source_priority",
                    _lyrics_src, ["qq_first", "ne_first", "qq_only", "ne_only"],
                    labels=_lyrics_labels, width=30); row += 1
        _lyrics_hint = ttk.Label(sf.inner,
            text="※ 当前源无歌词时按优先级自动回退到下一源",
            foreground="#888844", background="#1e1e2e",
            font=("Microsoft YaHei", 9))
        _lyrics_hint.grid(row=row, column=1, columnspan=2, sticky="w", padx=4, pady=(0, 4)); row += 1

        ttk.Label(sf.inner, text="— 字体 —", foreground="#8888cc",
                  background="#1e1e2e").grid(row=row, column=0, columnspan=2, sticky="w", padx=8, pady=(8, 2)); row += 1
        self._font_pick(sf, row, "字体", "lyrics", "font_family",
                        node.get("font_family", "Microsoft YaHei")); row += 1
        self._slider(sf, row, "字号", self._nk("lyrics", "font_size"),
                     node.get("font_size", 14), 6, 72, 1, fmt=".0f"); row += 1
        self._check(sf, row, "粗体", self._nk("lyrics", "font_bold"),
                    node.get("font_bold", False)); row += 1
        self._color(sf, row, "文字颜色", self._nk("lyrics", "color"),
                    node.get("color", "#cccccc")); row += 1

        # Outline
        ttk.Label(sf.inner, text="— 描边 —", foreground="#8888cc",
                  background="#1e1e2e").grid(row=row, column=0, columnspan=2, sticky="w", padx=8, pady=(8, 2)); row += 1
        lyc_outline_color = node.get("outline_color", "")
        self._color(sf, row, "描边颜色", self._nk("lyrics", "outline_color"),
                    lyc_outline_color or "#000000"); row += 1
        self._slider(sf, row, "描边宽度", self._nk("lyrics", "outline_width"),
                     int(node.get("outline_width", 0)), 0, 5, 1); row += 1

        self._slider(sf, row, "最大宽度", self._nk("lyrics", "max_width"),
                     node.get("max_width", 350), 50, 800, 10); row += 1
        self._combo(sf, row, "文本对齐 (左/中/右)", self._nk("lyrics", "alignment"),
                    node.get("alignment", "center"),
                    ["left", "center", "right"]); row += 1
        self._combo(sf, row, "显示行数", self._nk("lyrics", "max_lines"),
                    int(float(node.get("max_lines", 1))), [1, 2]); row += 1
        self._check(sf, row, "双语显示 ⚠ 翻译来自NetEase API，部分歌曲可能无翻译",
                    self._nk("lyrics", "bilingual"),
                    node.get("bilingual", True)); row += 1

        # Translation styling
        ttk.Label(sf.inner, text="— 翻译样式 —", foreground="#8888cc",
                  background="#1e1e2e").grid(row=row, column=0, columnspan=2, sticky="w", padx=8, pady=(8, 2)); row += 1
        self._combo(sf, row, "双语模式", self._nk("lyrics", "bilingual_mode"),
                    node.get("bilingual_mode", "merged"),
                    ["merged", "separate"]); row += 1
        self._color(sf, row, "翻译颜色", self._nk("lyrics", "trans_color"),
                    node.get("trans_color", "")); row += 1
        self._slider(sf, row, "翻译字号", self._nk("lyrics", "trans_font_size"),
                     int(float(node.get("trans_font_size", 0))), 0, 48, 1, fmt=".0f"); row += 1
        self._font_pick(sf, row, "翻译字体", "lyrics", "trans_font_family",
                        node.get("trans_font_family", "")); row += 1

        self._slider(sf, row, "时间偏移(ms)", self._nk("lyrics", "time_offset_ms"),
                     node.get("time_offset_ms", 0), -5000, 5000, 100); row += 1

        # Position
        ttk.Label(sf.inner, text="— 位置 —", foreground="#8888cc",
                  background="#1e1e2e").grid(row=row, column=0, columnspan=2, sticky="w", padx=8, pady=(8, 2)); row += 1
        self._pos_xy(sf, row, "lyrics"); row += 2

        # Lyrics animation
        ttk.Label(sf.inner, text="— 歌词动画 —", foreground="#8888cc",
                  background="#1e1e2e").grid(row=row, column=0, columnspan=2, sticky="w", padx=8, pady=(8, 2)); row += 1
        self._combo(sf, row, "动画效果", self._nk("lyrics", "animation"),
                    node.get("animation", "crossfade"), ["none", "scroll", "crossfade"])

    # ================================================================
    # Tab: Progress
    # ================================================================
    def _player_has_timeline(self) -> bool:
        """Check if the current SMTC player provides timeline position/duration."""
        try:
            _s = getattr(self.reader, '_smtc_engine', None)
            if _s is not None:
                return _s.player_has_timeline
        except Exception as e:
            _set_log.warn(f"_player_has_timeline: {e}")
        return True  # assume capable if unknown

    def _no_timeline_banner(self, sf) -> None:
        """Show a banner explaining that time/progress features are unavailable."""
        banner = tk.Frame(sf.inner, bg="#554400", relief="solid", bd=1)
        banner.pack(fill="x", padx=8, pady=12)
        tk.Label(banner, text="⚠ 当前播放源不提供播放进度",
                 fg="#ffcc44", bg="#554400",
                 font=("Microsoft YaHei", 11, "bold")).pack(pady=(8, 2))
        tk.Label(banner, text="时间进度和时长显示已自动关闭",
                 fg="#ddbb44", bg="#554400",
                 font=("Microsoft YaHei", 10)).pack()
        tk.Label(banner, text="切换至 QQ 音乐可恢复完整功能",
                 fg="#aaaa88", bg="#554400",
                 font=("Microsoft YaHei", 9)).pack(pady=(0, 8))

    def _tab_progress(self):
        sf = self._scroll_frame(" 进度 ")

        # ★ No timeline → show notice, hide all controls
        if not self._player_has_timeline():
            self._no_timeline_banner(sf)
            return

        node = self._node("progress")
        tt = node.setdefault("time_text", {})
        bar_cfg = node.setdefault("bar", {})
        arc_cfg = node.setdefault("arc", {})

        # Track row indices and widget visibility by mode
        self._pg_rows: dict[str, list[int]] = {
            "bar": [],        # rows only visible in bar mode
            "arc": [],        # rows only visible in arc/ring/roundrect mode
            "all": [],        # rows always visible
        }
        row = 0

        self._check(sf, row, "显示进度", self._nk("progress", "visible"),
                    node.get("visible", True)); row += 1

        # -- Appearance (all modes) --
        _r = row; row += 1
        ttk.Label(sf.inner, text="— 外观 —", foreground="#8888cc",
                  background="#1e1e2e").grid(row=_r, column=0, columnspan=2, sticky="w", padx=8, pady=(8, 2))
        self._pg_rows["all"].append(_r)

        self._combo(sf, row, "进度模式", self._nk("progress", "mode"),
                    node.get("mode", "bar"), ["off", "bar", "arc", "ring", "roundrect", "square"]); row += 1
        self._pg_rows["all"].append(row - 1)

        # ★ Hook mode combo change to show/hide mode-specific controls
        mode_var = self._vars.get(self._nk("progress", "mode"))
        if mode_var:
            mode_var.trace_add("write", lambda *a: self._on_progress_mode_changed(sf))

        self._color(sf, row, "进度颜色", self._nk("progress", "color"),
                    node.get("color", "#44cc88")); row += 1
        self._pg_rows["all"].append(row - 1)

        self._color(sf, row, "背景颜色", self._nk("progress", "background_color"),
                    node.get("background_color", "#333355")); row += 1
        self._pg_rows["all"].append(row - 1)

        # Line width / bar height (shared, but label changes per mode)
        _r_w = row; row += 1
        self._slider(sf, _r_w, "线条宽度", self._nk("progress", "width"),
                     node.get("width", 4), 1, 20, 1)
        self._pg_rows["all"].append(_r_w)
        self._pg_width_label = sf.inner.grid_slaves(row=_r_w, column=0)
        self._pg_width_row = _r_w

        # ---- Bar mode specific ----
        _r = row; row += 1
        ttk.Label(sf.inner, text="— Bar 模式 —", foreground="#8888cc",
                  background="#1e1e2e").grid(row=_r, column=0, columnspan=2, sticky="w", padx=8, pady=(8, 2))
        self._pg_rows["bar"].append(_r)

        self._slider(sf, row, "长度比例", self._nk("progress", "bar.length_ratio"),
                     float(bar_cfg.get("length_ratio", 0.7)), 0.1, 1.0, 0.05); row += 1
        self._pg_rows["bar"].append(row - 1)

        self._combo(sf, row, "填充方向 (lr=左→右, rl=右→左)", self._nk("progress", "bar.direction"),
                    bar_cfg.get("direction", "lr"),
                    ["lr", "rl"]); row += 1
        self._pg_rows["bar"].append(row - 1)

        self._combo(sf, row, "起始位置", self._nk("progress", "bar.start_position"),
                    bar_cfg.get("start_position", "left"),
                    ["left", "right"]); row += 1
        self._pg_rows["bar"].append(row - 1)

        # ---- Arc/Ring/Roundrect mode specific ----
        _r = row; row += 1
        ttk.Label(sf.inner, text="— Arc / Ring / Roundrect 模式 —", foreground="#8888cc",
                  background="#1e1e2e").grid(row=_r, column=0, columnspan=2, sticky="w", padx=8, pady=(8, 2))
        self._pg_rows["arc"].append(_r)

        self._combo(sf, row, "起始位置", self._nk("progress", "arc.start_position"),
                    arc_cfg.get("start_position", "top"),
                    ["top", "top-right", "right", "bottom-right",
                     "bottom", "bottom-left", "left", "top-left"]); row += 1
        self._pg_rows["arc"].append(row - 1)

        self._combo(sf, row, "旋转方向 (cw=顺时针, ccw=逆时针)", self._nk("progress", "arc.direction"),
                    arc_cfg.get("direction", "cw"),
                    ["cw", "ccw"]); row += 1
        self._pg_rows["arc"].append(row - 1)

        self._combo(sf, row, "端点样式 (round=圆头, flat=平头)", self._nk("progress", "arc.cap_style"),
                    arc_cfg.get("cap_style", "round"), ["round", "flat"]); row += 1
        self._pg_rows["arc"].append(row - 1)

        self._combo(sf, row, "相对封面位置 (edge=边缘, inside=内, outside=外)", self._nk("progress", "arc.position"),
                    arc_cfg.get("position", "edge"),
                    ["edge", "inside", "outside"]); row += 1
        self._pg_rows["arc"].append(row - 1)

        self._slider(sf, row, "与边缘距离(px)", self._nk("progress", "arc.distance"),
                     arc_cfg.get("distance", 0), 0, 80, 1, fmt=".0f"); row += 1
        self._pg_rows["arc"].append(row - 1)

        ss_raw_pg = arc_cfg.get("supersample", "2X")
        if not isinstance(ss_raw_pg, str):
            ss_raw_pg = f"{int(ss_raw_pg)}X"
        self._combo(sf, row, "超采样(进度)", self._nk("progress", "arc.supersample"),
                    ss_raw_pg, ["2X", "4X", "6X"]); row += 1
        self._pg_rows["arc"].append(row - 1)

        # -- Time text (all modes) --
        _r = row; row += 1
        ttk.Label(sf.inner, text="— 时间文本 —", foreground="#8888cc",
                  background="#1e1e2e").grid(row=_r, column=0, columnspan=2, sticky="w", padx=8, pady=(8, 2))
        self._pg_rows["all"].append(_r)

        self._check(sf, row, "显示时间", self._nk("progress", "time_text.visible"),
                    tt.get("visible", True)); row += 1
        self._pg_rows["all"].append(row - 1)

        self._combo(sf, row, "时间位置", self._nk("progress", "time_text.position"),
                    tt.get("position", "bottom"),
                    ["inside", "left", "right", "top", "bottom",
                     "top-left", "top-right", "bottom-left", "bottom-right"]); row += 1
        self._pg_rows["all"].append(row - 1)

        self._font_pick(sf, row, "时间字体", "progress", "time_text.font.family",
                        tt.get("font", {}).get("family", "Microsoft YaHei")); row += 1
        self._pg_rows["all"].append(row - 1)

        self._slider(sf, row, "时间字号", self._nk("progress", "time_text.font.size"),
                     tt.get("font", {}).get("size", 8), 6, 24, 1); row += 1
        self._pg_rows["all"].append(row - 1)

        self._color(sf, row, "时间颜色", self._nk("progress", "time_text.color"),
                    tt.get("color", "#cccccc")); row += 1
        self._pg_rows["all"].append(row - 1)

        # Time text outline
        tt_outline_color = tt.get("outline_color", "")
        tt_outline_width = int(float(tt.get("outline_width", 0)))
        if not tt_outline_color:
            tt_outline_color = "#000000"
        self._color(sf, row, "时间描边颜色", self._nk("progress", "time_text.outline_color"),
                    tt_outline_color); row += 1
        self._pg_rows["all"].append(row - 1)
        self._slider(sf, row, "时间描边宽度", self._nk("progress", "time_text.outline_width"),
                     tt_outline_width, 0, 3, 1); row += 1
        self._pg_rows["all"].append(row - 1)

        # Time text fine-tune offset
        ttk.Label(sf.inner, text="— 时间微调偏移 —", foreground="#8888cc",
                  background="#1e1e2e").grid(row=row, column=0, columnspan=2, sticky="w", padx=8, pady=(8, 2)); row += 1
        self._slider(sf, row, "水平偏移 X", self._nk("progress", "time_text.offset_x"),
                     int(float(tt.get("offset_x", 0))), -300, 300, 1); row += 1
        self._pg_rows["all"].append(row - 1)
        self._slider(sf, row, "垂直偏移 Y", self._nk("progress", "time_text.offset_y"),
                     int(float(tt.get("offset_y", 0))), -300, 300, 1); row += 1
        self._pg_rows["all"].append(row - 1)

        # -- Bar position (Fix #5: clarify this is for bar mode) --
        _r = row + 1; row += 2
        ttk.Label(sf.inner, text="— Bar进度条位置 —", foreground="#8888cc",
                  background="#1e1e2e").grid(row=_r, column=0, columnspan=2, sticky="w", padx=8, pady=(8, 2))
        self._pg_rows["bar"].append(_r)
        self._pos_xy(sf, row, "progress")
        self._pg_rows["bar"].append(row)
        self._pg_rows["bar"].append(row + 1)

        # Apply initial mode visibility
        self._on_progress_mode_changed(sf)

    def _on_progress_mode_changed(self, sf) -> None:
        """Show/hide controls based on selected progress mode."""
        mode_var = self._vars.get(self._nk("progress", "mode"))
        if not mode_var:
            return
        mode = mode_var.get()
        is_bar = (mode == "bar")
        is_arc = (mode in ("arc", "ring", "roundrect", "square"))

        # Toggle row visibility for bar-specific rows
        for r in self._pg_rows.get("bar", []):
            self._grid_row_visible(sf.inner, r, is_bar)
        # Toggle row visibility for arc-specific rows
        for r in self._pg_rows.get("arc", []):
            self._grid_row_visible(sf.inner, r, is_arc)

        # Update width/thickness label based on mode
        for widget in sf.inner.grid_slaves(row=self._pg_width_row, column=0):
            try:
                widget.configure(text="进度条高度" if is_bar else "线条宽度")
            except Exception:
                pass

    @staticmethod
    def _grid_row_visible(parent, row: int, visible: bool) -> None:
        """Show or hide all widgets in a grid row."""
        for widget in parent.grid_slaves(row=row):
            if visible:
                widget.grid()
            else:
                widget.grid_remove()

    # ================================================================
    # Tab: Progress Time (standalone)
    # ================================================================
    def _tab_progress_time(self):
        sf = self._scroll_frame(" 时间 ")

        if not self._player_has_timeline():
            self._no_timeline_banner(sf)
            return

        node = self._node("progress_time")
        row = 0

        self._check(sf, row, "显示时间", self._nk("progress_time", "visible"),
                    node.get("visible", True)); row += 1

        ttk.Label(sf.inner, text="— 字体 —", foreground="#8888cc",
                  background="#1e1e2e").grid(row=row, column=0, columnspan=2, sticky="w", padx=8, pady=(8, 2)); row += 1
        self._font_pick(sf, row, "字体", "progress_time", "font_family",
                        node.get("font_family", "Microsoft YaHei")); row += 1
        self._slider(sf, row, "字号", self._nk("progress_time", "font_size"),
                     node.get("font_size", 10), 6, 48, 1, fmt=".0f"); row += 1
        self._check(sf, row, "粗体", self._nk("progress_time", "font_bold"),
                    node.get("font_bold", False)); row += 1
        self._color(sf, row, "文字颜色", self._nk("progress_time", "color"),
                    node.get("color", "#cccccc")); row += 1

        ttk.Label(sf.inner, text="— 格式 —", foreground="#8888cc",
                  background="#1e1e2e").grid(row=row, column=0, columnspan=2, sticky="w", padx=8, pady=(8, 2)); row += 1
        self._combo(sf, row, "显示格式", self._nk("progress_time", "format"),
                    node.get("format", "elapsed_total"),
                    ["elapsed_total", "elapsed", "remaining"]); row += 1
        self._entry(sf, row, "分隔符", self._nk("progress_time", "separator"),
                    node.get("separator", " / "), 10); row += 1

        # Effects
        ttk.Label(sf.inner, text="— 效果 —", foreground="#8888cc",
                  background="#1e1e2e").grid(row=row, column=0, columnspan=2, sticky="w", padx=8, pady=(8, 2)); row += 1
        outline_color_val = node.get("outline_color", "")
        outline_width_val = node.get("outline_width", 0)
        if not outline_color_val:
            outline_color_val = "#000000"
        self._color(sf, row, "描边颜色", self._nk("progress_time", "outline_color"),
                    outline_color_val); row += 1
        self._slider(sf, row, "描边宽度", self._nk("progress_time", "outline_width"),
                     outline_width_val, 0, 5, 1); row += 1

        # Position
        ttk.Label(sf.inner, text="— 位置 —", foreground="#8888cc",
                  background="#1e1e2e").grid(row=row, column=0, columnspan=2, sticky="w", padx=8, pady=(8, 2)); row += 1
        self._pos_xy(sf, row, "progress_time")

    # ================================================================
    # Tab: Animation
    # ================================================================
    def _tab_animation(self):
        sf = self._scroll_frame(" 动画 ")
        anim = self._w.setdefault("animation", {})
        row = 0

        # Global
        ttk.Label(sf.inner, text="— 整体动画 —", foreground="#8888cc",
                  background="#1e1e2e").grid(row=row, column=0, columnspan=2, sticky="w", padx=8, pady=(8, 2)); row += 1
        self._slider(sf, row, "淡入时长(ms)", self._kp("animation.fade_in_ms"),
                     anim.get("fade_in_ms", 400), 100, 3000, 50); row += 1
        self._slider(sf, row, "淡出时长(ms)", self._kp("animation.fade_out_ms"),
                     anim.get("fade_out_ms", 800), 100, 3000, 50); row += 1
        self._slider(sf, row, "显示停留(s)", self._kp("animation.stay_seconds"),
                     anim.get("stay_seconds", 8), 2, 120, 1); row += 1
        self._check(sf, row, "常驻显示(不自动隐藏) ⚠ 关闭后窗口将在几秒后自动隐藏",
                    self._kp("animation.stay_forever"),
                    anim.get("stay_forever", False)); row += 1
        self._combo(sf, row, "渲染帧率", self._kp("animation.fps"),
                    int(anim.get("fps", 60)), [15, 30, 60, 120]); row += 1
        hint = ttk.Label(sf.inner, text="⚠ 更改后需重启应用生效", foreground="#cc8844",
                         background="#1e1e2e", font=("Microsoft YaHei", 9))
        hint.grid(row=row, column=1, columnspan=2, sticky="w", padx=4, pady=(0, 4)); row += 1

        # Continuous FPS mode: cover spin power saving
        try:
            _cont_cap = int(str(anim.get("continuous_fps_cap", 45)).split()[0])
        except (ValueError, IndexError):
            _cont_cap = 45
        _cont_label = {"eco": f"节能模式（封面旋转上限{_cont_cap}fps，节省GPU）",
                       "full": "保持全速（封面旋转跟随渲染帧率，性能开销较大）"}
        _cont_mode = anim.get("continuous_fps_mode", "eco")
        self._combo(sf, row, "旋转动画帧率", self._kp("animation.continuous_fps_mode"),
                    _cont_mode, ["eco", "full"], labels=_cont_label, width=30); row += 1
        hint2 = ttk.Label(sf.inner, text="※ 全速模式在120fps下GPU占用可达30%，建议4060及以上显卡使用",
                         foreground="#cc8844", background="#1e1e2e",
                         font=("Microsoft YaHei", 9))
        hint2.grid(row=row, column=1, columnspan=2, sticky="w", padx=4, pady=(0, 4)); row += 1

        # Continuous FPS cap (only applies in eco mode)
        _cap_labels = {"30": "30 fps（最大化节能，轻微不流畅）",
                       "45": "45 fps（平衡节能与流畅度）",
                       "60": "60 fps（流畅但GPU占用较高）"}
        self._combo(sf, row, "节能模式帧率上限", self._kp("animation.continuous_fps_cap"),
                    str(_cont_cap), ["30", "45", "60"], labels=_cap_labels, width=30); row += 1
        hint_cap = ttk.Label(sf.inner,
            text="※ 仅节能模式生效。较低帧率降低GPU占用，45fps为推荐平衡值",
            foreground="#888844", background="#1e1e2e",
            font=("Microsoft YaHei", 9))
        hint_cap.grid(row=row, column=1, columnspan=2, sticky="w", padx=4, pady=(0, 4)); row += 1

        # Fullscreen power saving
        _fs_labels = {"pause": "暂停旋转动画（全屏覆盖时停止封面旋转）",
                      "30fps": "降至30fps（全屏覆盖时封面旋转上限30fps）",
                      "15fps": "降至15fps（全屏覆盖时封面旋转上限15fps）"}
        _fs_mode = anim.get("fullscreen_eco_mode", "pause")
        self._combo(sf, row, "全屏覆盖时旋转动画", self._kp("animation.fullscreen_eco_mode"),
                    _fs_mode, ["pause", "30fps", "15fps"], labels=_fs_labels, width=35); row += 1
        hint3 = ttk.Label(sf.inner,
            text="※ 仅窗口置顶关闭时生效。检测到全屏应用（游戏/视频）时自动应用",
            foreground="#888844", background="#1e1e2e",
            font=("Microsoft YaHei", 9))
        hint3.grid(row=row, column=1, columnspan=2, sticky="w", padx=4, pady=(0, 4)); row += 1

        avail = self._available_renderers()
        renderer_val = anim.get("renderer", "pil")
        self._combo(sf, row, "渲染后端 ⚡", self._kp("animation.renderer"),
                    renderer_val, avail); row += 1
        gpu_note = "OpenGL=GPU加速" if "opengl" in avail else "OpenGL 不可用(需 pip install pyglet)"
        ttk.Label(sf.inner, text=f"※ pil=CPU软渲染 | {gpu_note} | 切换需重启",
                  foreground="#888844", background="#1e1e2e",
                  font=("Microsoft YaHei", 9)).grid(
            row=row, column=0, columnspan=3, sticky="w", padx=8, pady=(0, 4)); row += 1
        self._slider(sf, row, "数据刷新(ms)", self._kp("behavior.refresh_interval_ms"),
                     self._w.get("behavior", {}).get("refresh_interval_ms", 100), 20, 2000, 10, heavy=True)

    # ================================================================
    # Detection priority builder (shared by animation tab)
    # ================================================================

    _PLAYER_LABELS = {"qqmusic": "QQ音乐", "netease": "网易云音乐"}
    _PLAYER_CHECKERS = {
        "qqmusic": lambda: __import__('core.reader', fromlist=['_is_qqmusic_running'])._is_qqmusic_running(),
        "netease": lambda: __import__('core.reader', fromlist=['_is_netease_running'])._is_netease_running(),
    }

    _PLAYER_INFO = {
        "qqmusic": {
            "name": "QQ音乐", "lyrics": True, "progress": True, "cover": True,
            "notes": [
                "⚠ QQ音乐 API 不支持 7 分钟以上长音乐的播放进度精确定时",
            ],
        },
        "netease": {
            "name": "网易云音乐", "lyrics": True, "progress": False, "cover": True,
            "notes": [
                "⚠ 网易云音乐 SMTC 不提供播放进度数据，无法显示进度条和时间",
            ],
        },
    }

    def _build_detection_priority(self, sf) -> None:
        """Build sortable priority list with auto-detect and manual refresh."""
        wrap = ttk.Frame(sf.inner)
        wrap.pack(fill="x", padx=8, pady=(8, 4))

        ttk.Label(wrap, text="— 检测优先级 —", foreground="#8888cc",
                  background="#1e1e2e").pack(anchor="w", pady=(0, 4))
        ttk.Label(wrap, text="拖动排序 · 上方优先 · 多源同播时决定显示数据",
                  foreground="#888888", background="#1e1e2e",
                  font=("Microsoft YaHei", 8)).pack(anchor="w")

        # Listbox + buttons + info panel (horizontal row)
        main_row = tk.Frame(wrap, bg="#1e1e2e")
        main_row.pack(fill="x", pady=(4, 2))

        # Left: listbox + up/down
        lb_frame = tk.Frame(main_row, bg="#1e1e2e")
        lb_frame.pack(side="left")

        self._priority_listbox = tk.Listbox(
            lb_frame, bg="#2a2a3e", fg="#cccccc", selectbackground="#4444aa",
            font=("Microsoft YaHei", 10), width=16, height=4,
            exportselection=False, activestyle="none")
        self._priority_listbox.pack(side="left", fill="y")
        self._priority_listbox.bind("<<ListboxSelect>>",
                                     lambda e: self._show_player_info())

        btn_col = tk.Frame(lb_frame, bg="#1e1e2e")
        btn_col.pack(side="left", padx=4)
        tk.Button(btn_col, text="▲", width=3, bg="#4444aa", fg="white",
                  font=("Microsoft YaHei", 8, "bold"),
                  command=self._priority_move_up).pack(pady=1)
        tk.Button(btn_col, text="▼", width=3, bg="#4444aa", fg="white",
                  font=("Microsoft YaHei", 8, "bold"),
                  command=self._priority_move_down).pack(pady=1)

        # Right: info panel
        self._player_info_frame = tk.Frame(main_row, bg="#2a2a3e",
                                           relief="solid", bd=1)
        self._player_info_frame.pack(side="left", padx=12, fill="x", expand=True)
        self._player_info_labels: dict[str, tk.Label] = {}

        # Detect button + status
        btn_row = tk.Frame(wrap, bg="#1e1e2e")
        btn_row.pack(fill="x", pady=(4, 0))
        tk.Button(btn_row, text="检测可用源", bg="#446644", fg="white",
                  font=("Microsoft YaHei", 9),
                  command=lambda: self._detect_available_sources()).pack(side="left")
        self._priority_status_label = ttk.Label(
            btn_row, text="", foreground="#888844", background="#1e1e2e",
            font=("Microsoft YaHei", 9))
        self._priority_status_label.pack(side="left", padx=12)

        # "Don't ask again" checkbox
        chk_row = tk.Frame(wrap, bg="#1e1e2e")
        chk_row.pack(fill="x", pady=(4, 0))
        key = self._kp("behavior.suppress_missing_warnings")
        var = tk.BooleanVar(value=self._w.get("behavior", {}).get(
            "suppress_missing_warnings", False))
        self._vars[key] = var
        cb = tk.Checkbutton(chk_row, text="不再提示功能缺失",
                            variable=var, bg="#1e1e2e", fg="#aaaacc",
                            selectcolor="#1e1e2e", activebackground="#1e1e2e",
                            activeforeground="#cccccc",
                            command=self._sync,
                            font=("Microsoft YaHei", 9))
        cb.pack(anchor="w")

        self._refresh_priority_list()
        self._detect_available_sources()

    def _show_player_info(self) -> None:
        """Display capability info for the selected player."""
        lb = self._priority_listbox
        sel = lb.curselection()
        # Clear previous
        for w in self._player_info_frame.winfo_children():
            w.destroy()
        self._player_info_labels.clear()

        if not sel:
            tk.Label(self._player_info_frame, text="← 选择来源查看详情",
                     fg="#666688", bg="#2a2a3e",
                     font=("Microsoft YaHei", 9)).pack(pady=12)
            return

        text = lb.get(sel[0]).strip()
        label_to_key = {v["name"]: k for k, v in self._PLAYER_INFO.items()}
        key = label_to_key.get(text)
        info = self._PLAYER_INFO.get(key) if key else None
        if not info:
            return

        def _row(cap: str, ok: bool) -> None:
            icon = "✅" if ok else "❌"
            fg = "#44cc88" if ok else "#cc4444"
            fr = tk.Frame(self._player_info_frame, bg="#2a2a3e")
            fr.pack(fill="x", padx=8, pady=2)
            tk.Label(fr, text=f"{icon} {cap}", fg=fg, bg="#2a2a3e",
                     font=("Microsoft YaHei", 9)).pack(anchor="w")

        _row("歌词", info.get("lyrics", False))
        _row("实时播放进度", info.get("progress", False))
        _row("封面", info.get("cover", False))

        # ★ Source-specific notes
        _notes = info.get("notes", [])
        if _notes:
            tk.Frame(self._player_info_frame, height=6, bg="#2a2a3e").pack(fill="x")
            for _note in _notes:
                tk.Label(self._player_info_frame, text=_note,
                         fg="#cc8844", bg="#2a2a3e",
                         font=("Microsoft YaHei", 8),
                         wraplength=280, justify="left").pack(
                    anchor="w", padx=8, pady=1)

    def _refresh_priority_list(self) -> None:
        """Rebuild listbox — show all known players, ordered by config priority."""
        lb = self._priority_listbox
        lb.delete(0, "end")
        priority = self._w.get("behavior", {}).get("detection_priority", ["qqmusic", "netease"])
        # Show configured players in order, then any known players not yet listed
        shown = set()
        for p in priority:
            if p in self._PLAYER_LABELS:
                label = self._PLAYER_LABELS[p]
                lb.insert("end", f"  {label}")
                shown.add(p)
        for p, label in self._PLAYER_LABELS.items():
            if p not in shown:
                lb.insert("end", f"  {label}")

    def _priority_move_up(self) -> None:
        lb = self._priority_listbox
        sel = lb.curselection()
        if not sel or sel[0] == 0:
            return
        idx = sel[0]
        text = lb.get(idx)
        lb.delete(idx)
        lb.insert(idx - 1, text)
        lb.selection_set(idx - 1)
        self._save_priority_order()

    def _priority_move_down(self) -> None:
        lb = self._priority_listbox
        sel = lb.curselection()
        if not sel or sel[0] >= lb.size() - 1:
            return
        idx = sel[0]
        text = lb.get(idx)
        lb.delete(idx)
        lb.insert(idx + 1, text)
        lb.selection_set(idx + 1)
        self._save_priority_order()

    def _save_priority_order(self) -> None:
        """Save current listbox order to config (silent, no prompt)."""
        lb = self._priority_listbox
        order = []
        label_to_key = {v: k for k, v in self._PLAYER_LABELS.items()}
        for i in range(lb.size()):
            text = lb.get(i).strip()
            key = label_to_key.get(text, text)
            order.append(key)
        self._w.setdefault("behavior", {})["detection_priority"] = order

    def _detect_available_sources(self) -> None:
        """Check which players are currently running, update list."""
        parts = []
        for key, label in self._PLAYER_LABELS.items():
            try:
                running = self._PLAYER_CHECKERS[key]()
                status = "● 已运行" if running else "○ 未运行"
                color = "#44cc44" if running else "#888888"
                parts.append(f"{label} {status}")
            except Exception:
                parts.append(f"{label} ○ 未知")
        self._priority_status_label.configure(
            text=" | ".join(parts))
        if not any("●" in p for p in parts):
            self._priority_status_label.configure(
                foreground="#cc8844")
        else:
            self._priority_status_label.configure(
                foreground="#44cc44")

    # ================================================================
    # Tab: Sources — cover candidate selector
    # ================================================================
    def _tab_sources(self):
        sf = self._scroll_frame(" 来源 ")

        # Detection priority list
        self._build_detection_priority(sf)

        self._cover_cards_frame = ttk.Frame(sf.inner)
        self._cover_cards_frame.pack(fill="x", padx=4, pady=4)
        self._cover_thumb_photos = []
        self._cover_full_images = []  # full-resolution PIL images for export
        # ★ Force fresh data before first display
        self._force_refresh_covers()
        # _force_refresh_covers schedules a delayed rebuild, so also
        # build immediately with whatever is available now.
        self._rebuild_cover_cards()

        # Refresh + Export buttons + restore
        btn_frame = ttk.Frame(sf.inner)
        btn_frame.pack(fill="x", padx=8, pady=(4, 8))
        ttk.Button(btn_frame, text="🔄 刷新封面",
                   command=self._force_refresh_covers).pack(side="left", padx=4)
        ttk.Button(btn_frame, text="导出当前封面",
                   command=self._export_current_cover).pack(side="left", padx=4)
        ttk.Button(btn_frame, text="导出全部封面",
                   command=self._export_all_covers).pack(side="left", padx=4)
        # Restore button (hidden until auto-disable is applied)
        self._restore_label = tk.Label(btn_frame, text="⚠ 已自动关闭部分显示",
                                       fg="#cc8844", bg="#1e1e2e",
                                       font=("Microsoft YaHei", 9))
        self._restore_btn = tk.Button(btn_frame, text="恢复显示选项",
                                      bg="#cc8844", fg="white",
                                      font=("Microsoft YaHei", 9),
                                      command=self._restore_disabled)

        # Start polling for candidate changes
        self._source_poll_id = None
        self._start_source_poll()

    def _rebuild_cover_cards(self):
        for w in self._cover_cards_frame.winfo_children():
            w.destroy()
        self._cover_thumb_photos.clear()
        # Also keep PIL image references alive (tkinter needs both PhotoImage AND source PIL image)
        self._cover_pil_images = getattr(self, '_cover_pil_images', [])
        self._cover_pil_images.clear()
        self._cover_full_images = getattr(self, '_cover_full_images', [])
        self._cover_full_images.clear()

        if not self.reader:
            ttk.Label(self._cover_cards_frame, text="读取器未就绪").pack()
            return

        # ★ Build candidate list matching the priority order:
        #   1. SMTC (priority 1-2), 2. QQ Music API (priority 3), 3. Local (priority 4)
        candidates = []
        _reader_cands = self.reader.cover_candidates if self.reader else []

        # SMTC cover — highest priority (authoritative source)
        _live = self.reader.live_cover if self.reader else None
        if _live is not None:
            candidates.append(("SMTC封面", _live))

        # QQ Music API covers — second priority
        for _name, _img in _reader_cands:
            if "QQ" in _name and _name != "SMTC封面":
                candidates.append((_name, _img))

        # Local files — lowest priority
        for _name, _img in _reader_cands:
            if "QQ" not in _name and _name != "SMTC封面":
                candidates.append((_name, _img))
        if not candidates:
            ttk.Label(self._cover_cards_frame, text="暂无封面来源",
                     foreground="#888").pack(pady=20)
            return

        cfg_idx = self._w.get("behavior", {}).get("cover_source_index", 0)
        cfg_name = self._w.get("behavior", {}).get("cover_source_name", "")
        if cfg_idx == 0:
            # Auto mode: use reader's auto_cover_index (matches priority order:
            # SMTC高清 > SMTC缩略图 > QQ音乐API > 本地)
            _auto_idx = getattr(self.reader, 'auto_cover_index', -1)
            current_idx = _auto_idx if 0 <= _auto_idx < len(candidates) else -1
        elif cfg_name:
            # ★ Match by source NAME to handle index drift between reader's
            # _cover_candidates list and our local filtered candidates list.
            # e.g. reader has [SMTC, QQ] (QQ at idx 1) but local has [QQ, SMTC]
            # (QQ at idx 0). Matching by name avoids picking the wrong cover.
            current_idx = 0
            for i, (name, _img) in enumerate(candidates):
                if name == cfg_name:
                    current_idx = i
                    break
        else:
            current_idx = cfg_idx
        if current_idx < 0 or current_idx >= len(candidates):
            current_idx = 0

        thumb_size = 80
        cards_per_row = 3

        ttk.Label(self._cover_cards_frame, text="点击选择封面来源（自动选最优）",
                 foreground="#aaaacc", background="#1e1e2e").pack(anchor="w", pady=(0, 6))

        row_frame = None
        for idx, (name, img) in enumerate(candidates):
            if idx % cards_per_row == 0:
                row_frame = ttk.Frame(self._cover_cards_frame)
                row_frame.pack(fill="x", pady=2)

            is_current = (idx == current_idx)
            bg_color = "#4444aa" if is_current else "#2a2a3e"

            card = tk.Frame(row_frame, bg=bg_color, relief="solid", bd=1, cursor="hand2")
            card.pack(side="left", padx=3, fill="x", expand=True)
            card.bind("<Button-1>", lambda e, i=idx: self._on_cover_select(i))

            # ★ Store full-resolution image for export
            self._cover_full_images.append((name, img))

            photo = None
            try:
                thumb = img.copy()
                # Crop to square center
                s_min = min(thumb.size)
                left = (thumb.size[0] - s_min) // 2
                top = (thumb.size[1] - s_min) // 2
                thumb = thumb.crop((left, top, left + s_min, top + s_min))
                thumb = thumb.resize((thumb_size, thumb_size), Image.LANCZOS)
                # Keep PIL image alive (tkinter needs this)
                self._cover_pil_images.append(thumb)
                photo = ImageTk.PhotoImage(thumb)
                self._cover_thumb_photos.append(photo)
            except Exception as e:
                import traceback
                try:
                    import datetime
                    log_path = os.path.join(os.environ.get("TEMP", "."), "qqoverlay_ng.log")
                    with open(log_path, "a", encoding="utf-8") as f:
                        f.write(f"[{datetime.datetime.now():%H:%M:%S}] [Settings] thumb error: {e}\n{traceback.format_exc()[-200:]}\n")
                except Exception:
                    pass

            # Image label
            img_lbl = tk.Label(card, image=photo, bg=bg_color, width=thumb_size, height=thumb_size)
            img_lbl.pack(padx=2, pady=(4, 0))
            img_lbl.bind("<Button-1>", lambda e, i=idx: self._on_cover_select(i))

            # Name label
            display_name = name[:18] + ("…" if len(name) > 18 else "")
            name_lbl = tk.Label(card, text=display_name, bg=bg_color, fg="white",
                               font=("Microsoft YaHei", 7), wraplength=90)
            name_lbl.pack(pady=2)
            name_lbl.bind("<Button-1>", lambda e, i=idx: self._on_cover_select(i))

            if is_current:
                tk.Label(card, text="✓ 当前", bg="#4444aa", fg="#44cc88",
                        font=("Microsoft YaHei", 8, "bold")).pack()

    def _on_cover_select(self, idx):
        candidates = self.reader.cover_candidates if self.reader else []
        name = str(candidates[idx][0]) if 0 <= idx < len(candidates) else ""
        self._w.setdefault("behavior", {})["cover_source_index"] = idx
        self._w["behavior"]["cover_source_name"] = name  # sticky by name, not index
        self._sync_and_save()
        self._rebuild_cover_cards()

        # ★ Offer to save as preferred cover for the current song
        song = self.reader.song_name if self.reader else ""
        artist = self.reader.artist_name if self.reader else ""
        # Skip prompt for QQ Music online cover (it's the default auto-match)
        is_default_web = name.startswith("QQ音乐在线") or name.startswith("QQ闊充箰鍦ㄧ嚎")
        if song and name and not is_default_web:
            try:
                from core.cover_prefs import get_preferred_source
                if get_preferred_source(song, artist) == name:
                    return  # Already saved
            except Exception as e:
                _set_log.warn(f"cover pref check: {e}")
            self.window.after(300, lambda: self._ask_save_preference(song, artist, name))

    def _ask_save_preference(self, song: str, artist: str, source_name: str) -> None:
        """Ask user if they want to save this cover for the current song."""
        if not self.window or not self.window.winfo_exists():
            return
        import tkinter.messagebox as tkmb
        short_name = source_name[:40] + ("…" if len(source_name) > 40 else "")
        song_display = song[:25] + ("…" if len(song) > 25 else "")
        want = tkmb.askyesno(
            "封面偏好记忆",
            f"是否将当前封面关联到：\n\n"
            f"  「{song_display}」\n - {artist[:25] if artist else '未知'}\n\n"
            f"封面来源: {short_name}\n\n"
            f"下次播放此歌曲时将自动使用该封面。",
            parent=self.window)
        if want:
            try:
                from core.cover_prefs import save_preference
                save_preference(song, artist, source_name)
            except Exception as e:
                _set_log.warn(f"cover pref save: {e}")

    # ================================================================
    # Cover export
    # ================================================================

    def _force_refresh_covers(self) -> None:
        """Manually refresh cover: force SMTC poll + sync reader + gather + rebuild."""
        if not self.reader:
            return
        try:
            _s = getattr(self.reader, '_smtc_engine', None)
            if _s is not None:
                _s.force_poll()
        except Exception as e:
            _set_log.warn(f"SMTC force_poll: {e}")
        # ★ Sync reader from SMTC: the settings process has its own reader
        # whose _last_song/_last_artist are seeded once at startup and never
        # updated automatically. Without this manual sync, song_name and
        # _cover_candidates stay frozen at whatever was playing when settings
        # was opened.
        try:
            _s = getattr(self.reader, '_smtc_engine', None)
            if _s is not None:
                _smtc_title = (_s.title or "").strip()
                _smtc_artist = (_s.artist or "").strip()
                if _smtc_title and _smtc_title != self.reader._last_song:
                    self.reader._last_song = _smtc_title
                    self.reader._last_artist = _smtc_artist
                    self.reader._cover_is_stale = True
                    self.reader._cover_candidates = []
        except Exception as e:
            _set_log.warn(f"SMTC song sync: {e}")
        # ★ CRITICAL: _gather_cover_candidates() must run BEFORE _rebuild_cover_cards.
        # force_poll() only updates the SMTC engine; the reader's _cover_candidates
        # list still holds the previous song's QQ covers. Without an explicit gather,
        # _rebuild_cover_cards mixes old QQ covers with the fresh SMTC thumbnail,
        # and current_idx=0 picks the stale QQ cover for export.
        try:
            self.reader._gather_cover_candidates()
            # ★ Clear stale flag after successful gather. In the settings
            # process, the reader's update()/background threads never run,
            # so _cover_is_stale would stay True forever — causing
            # get_selected_cover() to skip _cover_candidates and fall
            # through to SMTC thumbnail (which returns a new copy with a
            # new id() each call), triggering _rebuild_cover_cards every
            # 500ms poll cycle → continuous flickering.
            self.reader._cover_is_stale = False
        except Exception as e:
            _set_log.warn(f"gather cover candidates: {e}")
        self._rebuild_cover_cards()
        if self.window:
            self.window.after(1000, self._rebuild_cover_cards)

    def _export_current_cover(self) -> None:
        """Export the cover currently displayed in the sources tab.

        ★ Rebuilds the candidate list from scratch at export time using the
        SAME logic as _rebuild_cover_cards — zero cached state, zero timing
        gaps. No matter how many times _rebuild_cover_cards has been called
        with stale data, this always exports what SHOULD be shown right now.
        """
        if not self.reader:
            tk.messagebox.showinfo("无封面", "当前没有可导出的封面", parent=self.window)
            return

        # --- Build candidates (same logic as _rebuild_cover_cards) ---
        candidates = []
        _reader_cands = self.reader.cover_candidates if self.reader else []

        # SMTC cover — highest priority
        _live = self.reader.live_cover if self.reader else None
        if _live is not None:
            candidates.append(("SMTC封面", _live))

        # QQ Music API covers — second priority
        for _name, _img in _reader_cands:
            if "QQ" in _name and _name != "SMTC封面":
                candidates.append((_name, _img))

        # Local files — lowest priority
        for _name, _img in _reader_cands:
            if "QQ" not in _name and _name != "SMTC封面":
                candidates.append((_name, _img))

        if not candidates:
            tk.messagebox.showinfo("无封面", "当前没有可导出的封面", parent=self.window)
            return

        # --- Resolve current index (same logic as _rebuild_cover_cards) ---
        cfg_idx = self._w.get("behavior", {}).get("cover_source_index", 0)
        cfg_name = self._w.get("behavior", {}).get("cover_source_name", "")
        if cfg_idx == 0:
            _auto_idx = getattr(self.reader, 'auto_cover_index', -1)
            if 0 <= _auto_idx < len(candidates):
                current_idx = _auto_idx
            else:
                tk.messagebox.showinfo(
                    "无可靠封面",
                    "当前自动模式下暂无可靠封面，正在等待 SMTC 或在线封面。",
                    parent=self.window,
                )
                return
        elif cfg_name:
            current_idx = 0
            for i, (name, _img) in enumerate(candidates):
                if name == cfg_name:
                    current_idx = i
                    break
        else:
            current_idx = cfg_idx
        if current_idx < 0 or current_idx >= len(candidates):
            current_idx = 0

        img = candidates[current_idx][1]
        song = self.reader.song_name or "cover"
        artist = self.reader.artist_name or ""
        self._save_cover_image(img, f"{artist} - {song}")

    def _export_all_covers(self) -> None:
        """Export live cover + all cache candidates."""
        imgs = []
        _live = self.reader.live_cover if self.reader else None
        if _live is not None:
            imgs.append(("SMTC封面", _live))
        for name, img in (self.reader.cover_candidates if self.reader else []):
            if "QQ" in name:
                imgs.append((name, img))
        if not imgs:
            tk.messagebox.showinfo("无封面", "当前没有可导出的封面", parent=self.window)
            return

        from tkinter import filedialog
        folder = filedialog.askdirectory(
            title="选择导出文件夹", parent=self.window)
        if not folder:
            return

        saved = 0
        for name, img in imgs:
            try:
                safe_name = "".join(c for c in name if c.isalnum()
                                   or c in "._- ()（）").strip()
                if not safe_name:
                    safe_name = f"cover_{saved}"
                path = os.path.join(folder, f"{safe_name}.jpg")
                img.convert("RGB").save(path, "JPEG", quality=92)
                saved += 1
            except Exception as e:
                _set_log.warn(f"cover export save: {e}")

        tk.messagebox.showinfo("导出完成",
                              f"已导出 {saved}/{len(imgs)} 张封面",
                              parent=self.window)

    def _save_cover_image(self, img, name: str) -> None:
        """Open save dialog and export a single cover image."""
        from tkinter import filedialog
        safe_name = "".join(c for c in name if c.isalnum()
                           or c in "._- ()（）").strip()
        if not safe_name:
            safe_name = "cover"
        path = filedialog.asksaveasfilename(
            title="导出封面", parent=self.window,
            initialfile=f"{safe_name}.jpg",
            filetypes=[("JPEG", "*.jpg"), ("PNG", "*.png"), ("All", "*.*")],
            defaultextension=".jpg")
        if not path:
            return

        try:
            fmt = "JPEG" if path.lower().endswith((".jpg", ".jpeg")) else "PNG"
            if fmt == "JPEG":
                img.convert("RGB").save(path, fmt, quality=92)
            else:
                img.save(path, fmt)
            tk.messagebox.showinfo("导出成功",
                                  f"封面已保存到:\n{path}",
                                  parent=self.window)
        except Exception as e:
            tk.messagebox.showerror("导出失败", str(e), parent=self.window)

    def _start_source_poll(self):
        self._source_poll_id = self.window.after(500, self._poll_cover_sources)

    def _poll_cover_sources(self):
        if not self.window or not self.window.winfo_exists():
            return
        prev_count = len(self.reader.cover_candidates) if self.reader else 0
        prev_song = getattr(self, '_last_cover_song', "")
        current_song = self.reader.song_name if self.reader else ""
        if self.reader:
            self.reader.update_api_results()
        cur_count = len(self.reader.cover_candidates) if self.reader else 0
        # ★ If candidates empty but song is playing, force a gather immediately.
        # Rate-limited to once per 3 seconds to avoid blocking the UI.
        if cur_count == 0 and self.reader and self.reader.song_name:
            _last_force = getattr(self, '_last_force_gather', 0)
            if time.time() - _last_force > 3:
                self._last_force_gather = time.time()
                try:
                    self.reader._gather_cover_candidates()
                    cur_count = len(self.reader.cover_candidates)
                except Exception as e:
                    _set_log.warn(f"_poll gather: {e}")
        # ★ Detect SMTC player change → rebuild progress/time tabs
        _prev_player = getattr(self, '_last_poll_player', "")
        _cur_player = ""
        try:
            _s = getattr(self.reader, '_smtc_engine', None)
            if _s is not None:
                _cur_player = _s.player
        except Exception as e:
            _set_log.warn(f"SMTC player check: {e}")
        if _cur_player != _prev_player:
            self._last_poll_player = _cur_player
            # Rebuild pages to reflect source-specific progress/time capabilities.
            self._rebuild()
            # Also force cover card refresh (new player = new SMTC thumbnail)
            self._rebuild_cover_cards()
        # ★ Always rebuild when stale status changes (new search completed)
        _cur_stale = getattr(self.reader, '_cover_is_stale', False) if self.reader else False
        _prev_stale = getattr(self, '_last_cover_stale', None)
        _stale_changed = (_cur_stale != _prev_stale)
        if _stale_changed:
            self._last_cover_stale = _cur_stale
            self._rebuild_cover_cards()
            self._last_cover_song = current_song
        # Rebuild on count/song change
        elif cur_count != prev_count or current_song != prev_song:
            # ★ Song changed — force a gather NOW so we don't rebuild with
            # stale _cover_candidates from the previous song.
            if current_song != prev_song and self.reader:
                try:
                    self.reader._gather_cover_candidates()
                    cur_count = len(self.reader.cover_candidates)
                except Exception as e:
                    _set_log.warn(f"_poll gather2: {e}")
            self._rebuild_cover_cards()
            self._last_cover_song = current_song
        # During stale period: force rebuild every 2s until new cover arrives
        elif _cur_stale:
            _last_stale_rebuild = getattr(self, '_last_stale_rebuild', 0)
            if time.time() - _last_stale_rebuild > 2:
                self._last_stale_rebuild = time.time()
                self._rebuild_cover_cards()
        else:
            # Detect cover image change even when count/song stay same
            try:
                _cur_cover_id = id(self.reader.get_selected_cover()) if self.reader else 0
                _prev_cover_id = getattr(self, '_last_cover_id', 0)
                if _cur_cover_id != _prev_cover_id:
                    self._last_cover_id = _cur_cover_id
                    self._rebuild_cover_cards()
            except Exception as e:
                _set_log.warn(f"cover change detect: {e}")
        # ★ Auto-refresh cover library when cache changes
        _lib_prev = getattr(self, '_cover_lib_count', 0)
        try:
            from core.cover_cache import get_covers
            _lib_cur = len(get_covers())
        except Exception:
            _lib_cur = _lib_prev
        if _lib_cur != _lib_prev:
            self._cover_lib_count = _lib_cur
            if hasattr(self, '_cover_lib_frame') and self._cover_lib_frame.winfo_exists():
                self._rebuild_cover_library()
        self._source_poll_id = self.window.after(500, self._poll_cover_sources)

    # ================================================================
    # Tab: Cover Library — cached cover browser
    # ================================================================
    def _tab_cover_library(self):
        sf = self._scroll_frame(" 封面库 ")

        # Toolbar
        bar = ttk.Frame(sf.inner)
        bar.pack(fill="x", padx=8, pady=4)
        ttk.Button(bar, text="🔄 刷新列表",
                   command=self._rebuild_cover_library).pack(side="left", padx=4)
        ttk.Button(bar, text="🗑 清空缓存",
                   command=self._clear_cover_cache).pack(side="left", padx=4)

        # Grid container
        self._cover_lib_frame = ttk.Frame(sf.inner)
        self._cover_lib_frame.pack(fill="both", expand=True, padx=4, pady=4)
        self._cover_lib_photos = []
        self._cover_lib_selected = set()  # selected indices

        self._rebuild_cover_library()

    def _rebuild_cover_library(self):
        for w in self._cover_lib_frame.winfo_children():
            w.destroy()
        self._cover_lib_photos.clear()
        self._cover_lib_pil = getattr(self, '_cover_lib_pil', [])
        self._cover_lib_pil.clear()
        self._cover_lib_selected.clear()

        try:
            from core.cover_cache import get_covers
            covers = get_covers()
        except Exception:
            covers = []

        if not covers:
            ttk.Label(self._cover_lib_frame, text="暂无缓存封面 — 播放音乐后自动收集",
                     foreground="#888").pack(pady=40)
            return

        ttk.Label(self._cover_lib_frame,
                  text=f"共 {len(covers)} 张封面 · 点击选择 · 右键导出",
                  foreground="#aaaacc", background="#1e1e2e").pack(anchor="w", pady=(0, 8))

        thumb_size = 100
        cols = 4
        row_frame = None

        for idx, entry in enumerate(covers):
            if idx % cols == 0:
                row_frame = ttk.Frame(self._cover_lib_frame)
                row_frame.pack(fill="x", pady=2)

            info = f"{entry.get('song','')[:12]}\n{entry.get('artist','')[:12]}"
            card = tk.Frame(row_frame, bg="#2a2a3e", relief="solid", bd=1,
                           cursor="hand2")
            card.pack(side="left", padx=3, fill="x", expand=True)

            photo = None
            try:
                from PIL import Image, ImageTk
                img = Image.open(entry["path"]).convert("RGBA")
                s = min(img.size)
                left = (img.size[0] - s) // 2
                top = (img.size[1] - s) // 2
                thumb = img.crop((left, top, left + s, top + s))
                thumb = thumb.resize((thumb_size, thumb_size), Image.LANCZOS)
                self._cover_lib_pil.append(thumb)
                photo = ImageTk.PhotoImage(thumb)
                self._cover_lib_photos.append(photo)
            except Exception:
                pass

            img_lbl = tk.Label(card, image=photo or "",
                              bg="#2a2a3e", width=thumb_size, height=thumb_size)
            if photo:
                img_lbl.pack(padx=2, pady=(4, 0))

            tk.Label(card, text=info, bg="#2a2a3e", fg="#cccccc",
                    font=("Microsoft YaHei", 7), justify="center").pack(pady=2)

            # Click to select (★ capture both idx and card by-value)
            card.bind("<Button-1>", lambda e, i=idx, c=card: self._on_cover_lib_select(i, c))
            img_lbl.bind("<Button-1>", lambda e, i=idx, c=card: self._on_cover_lib_select(i, c))
            # Right-click to export
            card.bind("<Button-3>", lambda e, i=idx: self._export_lib_cover(i))
            img_lbl.bind("<Button-3>", lambda e, i=idx: self._export_lib_cover(i))

        # Multi-export button
        ttk.Button(self._cover_lib_frame, text="导出选中封面",
                   command=self._export_selected_covers).pack(pady=6)

    def _on_cover_lib_select(self, idx, card):
        if idx in self._cover_lib_selected:
            self._cover_lib_selected.discard(idx)
            card.configure(bg="#2a2a3e")
        else:
            self._cover_lib_selected.add(idx)
            card.configure(bg="#4444aa")

    def _export_lib_cover(self, idx):
        try:
            from core.cover_cache import get_covers
            covers = get_covers()
            if 0 <= idx < len(covers):
                entry = covers[idx]
                from PIL import Image
                img = Image.open(entry["path"])
                self._save_cover_image(img, f"{entry.get('artist','')} - {entry.get('song','')}")
        except Exception as e:
            _set_log.warn(f"lib cover export: {e}")

    def _export_selected_covers(self):
        if not self._cover_lib_selected:
            tk.messagebox.showinfo("未选择", "请先点击选择要导出的封面", parent=self.window)
            return
        try:
            from core.cover_cache import get_covers
            covers = get_covers()
        except Exception:
            return
        from tkinter import filedialog
        folder = filedialog.askdirectory(title="选择导出文件夹", parent=self.window)
        if not folder:
            return
        saved = 0
        for idx in self._cover_lib_selected:
            if 0 <= idx < len(covers):
                try:
                    entry = covers[idx]
                    from PIL import Image
                    img = Image.open(entry["path"])
                    safe = "".join(c for c in f"{entry.get('artist','')} - {entry.get('song','')}"
                                  if c.isalnum() or c in "._- ()（）").strip() or f"cover_{idx}"
                    path = os.path.join(folder, f"{safe}.jpg")
                    img.convert("RGB").save(path, "JPEG", quality=92)
                    saved += 1
                except Exception as e:
                    _set_log.warn(f"cover export save: {e}")
        tk.messagebox.showinfo("导出完成", f"已导出 {saved}/{len(self._cover_lib_selected)} 张",
                              parent=self.window)

    def _clear_cover_cache(self):
        if not tk.messagebox.askyesno("确认清空", "确定删除所有缓存的封面？", parent=self.window):
            return
        try:
            from core.cover_cache import clear_cache
            n = clear_cache()
            self._rebuild_cover_library()
            tk.messagebox.showinfo("完成", f"已清空 {n} 张缓存封面", parent=self.window)
        except Exception as e:
            tk.messagebox.showerror("错误", str(e), parent=self.window)

    # ================================================================
    # Tab: Themes
    # ================================================================
    def _tab_themes(self):
        sf = self._scroll_frame(" 主题 ")

        save_f = ttk.LabelFrame(sf.inner, text="保存当前主题", padding=8)
        save_f.pack(fill="x", padx=4, pady=4)
        name_var = tk.StringVar()
        ttk.Entry(save_f, textvariable=name_var, width=25).pack(side="left", padx=4)
        ttk.Button(save_f, text="保存",
                   command=lambda: self._do_save_theme(name_var.get())).pack(side="left", padx=4)

        list_f = ttk.LabelFrame(sf.inner, text="已保存主题", padding=8)
        list_f.pack(fill="both", expand=True, padx=4, pady=4)
        for name in list_themes():
            row_f = ttk.Frame(list_f)
            row_f.pack(fill="x", pady=1)
            ttk.Label(row_f, text=name).pack(side="left", padx=4)
            ttk.Button(row_f, text="应用",
                       command=lambda n=name: self._do_load_theme(n)).pack(side="right", padx=2)
            ttk.Button(row_f, text="删除",
                       command=lambda n=name: self._do_delete_theme(n)).pack(side="right", padx=2)
            ttk.Button(row_f, text="重命名",
                       command=lambda n=name: self._do_rename_theme(n)).pack(side="right", padx=2)

    # ================================================================
    # Tab: About
    # ================================================================
    def _tab_about(self):
        sf = self._scroll_frame(" 关于 ")
        row = 0

        # Version
        from version import get_version, get_short_version, get_build_stamp
        ttk.Label(sf.inner, text="QQMusicOverlay NG", foreground="#ffffff",
                  background="#1e1e2e", font=("Microsoft YaHei", 16, "bold")
                  ).grid(row=row, column=0, columnspan=2, sticky="w", padx=12, pady=(12, 2)); row += 1
        ttk.Label(sf.inner, text=get_version(), foreground="#8888cc",
                  background="#1e1e2e", font=("Microsoft YaHei", 10)
                  ).grid(row=row, column=0, columnspan=2, sticky="w", padx=12, pady=(0, 2)); row += 1
        ttk.Label(sf.inner, text=f"构建: {get_build_stamp()}", foreground="#666688",
                  background="#1e1e2e", font=("Microsoft YaHei", 8)
                  ).grid(row=row, column=0, columnspan=2, sticky="w", padx=12, pady=(0, 8)); row += 1

        # Credit
        ttk.Label(sf.inner, text="@Arna_Shiyuki 詩雪", foreground="#cc8844",
                  background="#1e1e2e", font=("Microsoft YaHei", 10, "italic")
                  ).grid(row=row, column=0, columnspan=2, sticky="w", padx=12, pady=(0, 4)); row += 1

        # ★ BETA TEST badge
        ttk.Label(sf.inner, text="BETA TEST  测试版本，效果仅供参考",
                  foreground="#ff8844", background="#2a1a0e",
                  font=("Microsoft YaHei", 10, "bold")
                  ).grid(row=row, column=0, columnspan=2, sticky="w", padx=12, pady=(0, 8)); row += 1

        # Separator
        ttk.Separator(sf.inner, orient="horizontal").grid(
            row=row, column=0, columnspan=2, sticky="ew", padx=8, pady=4); row += 1

        # Log retention
        ttk.Label(sf.inner, text="— 日志保留 —", foreground="#8888cc",
                  background="#1e1e2e").grid(row=row, column=0, columnspan=2, sticky="w", padx=8, pady=(8, 2)); row += 1

        ml = self._w.get("behavior", {}).get("max_log_lines", 500)
        self._slider(sf, row, "每文件最多行数 (0=不限)", self._kp("behavior.max_log_lines"),
                     ml, 0, 2000, 100); row += 1

        ttk.Label(sf.inner, text=f"当前有效值: {ml} 行（重启后生效）", foreground="#888",
                  background="#1e1e2e", font=("Microsoft YaHei", 9)
                  ).grid(row=row, column=0, columnspan=2, sticky="w", padx=12, pady=(0, 4)); row += 1

        # Open log folder
        def _open_log_dir():
            import subprocess, os as _os
            log_dir = _os.environ.get("TEMP", ".")
            subprocess.Popen(["explorer", log_dir], shell=True)

        ttk.Button(sf.inner, text="打开日志文件夹", command=_open_log_dir
                   ).grid(row=row, column=0, sticky="w", padx=12, pady=(8, 4)); row += 1

        ttk.Label(sf.inner, text=f"日志路径: {os.environ.get('TEMP', '.')}\\qqoverlay_*.log",
                  foreground="#666", background="#1e1e2e", font=("Microsoft YaHei", 8)
                  ).grid(row=row, column=0, columnspan=2, sticky="w", padx=12, pady=(0, 4)); row += 1

        # Autostart
        ttk.Separator(sf.inner, orient="horizontal").grid(
            row=row, column=0, columnspan=2, sticky="ew", padx=8, pady=4); row += 1
        ttk.Label(sf.inner, text="— 开机启动 —", foreground="#8888cc",
                  background="#1e1e2e").grid(row=row, column=0, columnspan=2, sticky="w", padx=8, pady=(8, 2)); row += 1

        from config import is_autostart_enabled, set_autostart
        _auto_var = tk.BooleanVar(value=is_autostart_enabled())
        def _toggle_autostart():
            set_autostart(_auto_var.get())

        _cb = ttk.Checkbutton(sf.inner, text="开机自动启动 QQMusicOverlay NG",
                              variable=_auto_var, command=_toggle_autostart)
        _cb.grid(row=row, column=0, columnspan=2, sticky="w", padx=12, pady=4); row += 1

        _status = "已启用 — 快捷方式已添加到启动文件夹" if is_autostart_enabled() else "未启用"
        ttk.Label(sf.inner, text=_status, foreground="#666",
                  background="#1e1e2e", font=("Microsoft YaHei", 8)
                  ).grid(row=row, column=0, columnspan=2, sticky="w", padx=12, pady=(0, 12)); row += 1

    # ================================================================
    # Widget helpers
    # ================================================================

    def _scroll_frame(self, title: str):
        """Create a scrollable settings page in the right detail pane."""
        idx = self._nav_build_index
        self._nav_build_index += 1
        nav_title = _SETTINGS_NAV_TITLES[idx] if idx < len(_SETTINGS_NAV_TITLES) else title.strip()
        key = f"tab_{idx}_{nav_title}"

        outer = tk.Frame(self._content, bg=_SETTINGS_PANEL, highlightthickness=0)
        outer.grid(row=0, column=0, sticky="nsew")
        outer.grid_rowconfigure(2, weight=1)
        outer.grid_columnconfigure(0, weight=1)
        outer.grid_remove()

        header = tk.Frame(outer, bg=_SETTINGS_PANEL, highlightthickness=0)
        header.grid(row=0, column=0, sticky="ew", padx=18, pady=(16, 8))
        tk.Label(header, text=nav_title, bg=_SETTINGS_PANEL, fg=_SETTINGS_TEXT,
                 font=("Microsoft YaHei", 16, "bold"), anchor="w"
                 ).pack(side="left")
        tk.Frame(outer, bg=_SETTINGS_BORDER, height=1, highlightthickness=0
                 ).grid(row=1, column=0, sticky="ew", padx=18)

        body = tk.Frame(outer, bg=_SETTINGS_PANEL, highlightthickness=0)
        body.grid(row=2, column=0, sticky="nsew", padx=18, pady=(10, 14))
        body.grid_rowconfigure(0, weight=1)
        body.grid_columnconfigure(0, weight=1)

        canvas = tk.Canvas(body, bg=_SETTINGS_PANEL, highlightthickness=0,
                           borderwidth=0, relief="flat")
        scrollbar = ttk.Scrollbar(body, orient="vertical", command=canvas.yview)
        inner = ttk.Frame(canvas)
        inner.columnconfigure(1, weight=1)
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        inner_id = canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(inner_id, width=e.width))
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns", padx=(6, 0))

        btn = tk.Button(
            self._nav_buttons_frame,
            text=nav_title,
            command=lambda k=key: self._select_tab(k),
            anchor="w",
            padx=12,
            pady=8,
            bd=0,
            font=("Microsoft YaHei UI", 10),
            cursor="hand2",
        )
        btn.pack(fill="x", padx=6, pady=1)
        self._tab_pages[key] = outer
        self._tab_buttons[key] = btn
        self._tab_order.append(key)
        # Mouse wheel scrolling
        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        canvas.bind("<Enter>", lambda e: canvas.bind_all("<MouseWheel>", _on_mousewheel))
        canvas.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))
        result = type('ScrollFrame', (), {'inner': inner, 'canvas': canvas})()
        self._refresh_nav_buttons()
        return result

    # -- Backend helpers --
    def _available_renderers(self) -> list[str]:
        """Check which renderers are available. Returns list of keys."""
        from config import detect_available_renderers
        avail = detect_available_renderers()
        # Only expose opengl if pyglet is importable
        keys = ["pil"]
        if avail.get("opengl"):
            keys.append("opengl")
        return keys

    # -- Key path helpers --
    def _kp(self, path: str) -> str:
        """Top-level config key path like 'window.opacity'."""
        return path

    def _nk(self, eid: str, key: str) -> str:
        """Node-level key (stored in scene.nodes[].data for sync)."""
        return f"_node.{eid}.{key}"

    def _spin(self, key: str) -> str:
        """Cover spin key."""
        return self._nk("cover", f"spin.{key}")

    def _node(self, eid: str) -> dict:
        """Get or create the config node dict for an element."""
        nodes = self._w.setdefault("scene", {}).setdefault("nodes", [])
        for n in nodes:
            if n.get("id") == eid:
                return n
        # Not found — create default
        new_node = {"id": eid, "type": eid if eid != "cover" else "cover"}
        if eid in ("title", "artist"):
            new_node.update({"type": "text", "source": "song_name" if eid == "title" else "artist_name"})
        elif eid == "lyrics":
            new_node["type"] = "synced_lyrics"
        elif eid == "progress":
            new_node["type"] = "progress_bar"
        elif eid == "progress_time":
            new_node["type"] = "progress_time"
        nodes.append(new_node)
        return new_node

    # -- Widget factory --
    def _slider(self, sf, row, label: str, key: str, value, vmin, vmax, step,
                fmt: str | None = None, heavy: bool = False):
        ttk.Label(sf.inner, text=label, background="#1e1e2e",
                  foreground="#cccccc").grid(row=row, column=0, sticky="w", padx=8, pady=2)
        var = tk.DoubleVar(value=value)
        self._vars[key] = var
        scale = ttk.Scale(sf.inner, from_=vmin, to=vmax, variable=var, length=180)
        scale.grid(row=row, column=1, sticky="w", padx=4)
        if not heavy:
            scale.bind("<ButtonRelease-1>", lambda e: self._sync())
        else:
            scale.bind("<ButtonRelease-1>", lambda e: self._sync())

        # Numeric entry box next to slider
        entry_var = tk.StringVar(value=str(value))
        entry = ttk.Entry(sf.inner, textvariable=entry_var, width=7)
        entry.grid(row=row, column=2, sticky="w", padx=2)
        entry.bind("<Return>", lambda e, v=var, ev=entry_var, mn=vmin, mx=vmax, st=step:
                   self._on_entry_commit(v, ev, mn, mx, st))
        entry.bind("<FocusOut>", lambda e, v=var, ev=entry_var, mn=vmin, mx=vmax, st=step:
                   self._on_entry_commit(v, ev, mn, mx, st))
        # Sync entry when slider moves
        def _sync_entry(*a):
            v = var.get()
            if fmt == ".0%":
                entry_var.set(f"{v*100:.0f}")
            elif fmt == ".0f":
                entry_var.set(f"{v:.0f}")
            elif fmt == ".1f":
                entry_var.set(f"{v:.1f}")
            else:
                entry_var.set(f"{v:.1f}")
        var.trace_add("write", _sync_entry)
        _sync_entry()

    def _on_entry_commit(self, var, entry_var, vmin, vmax, step):
        """User typed a value in the entry box — validate and apply."""
        try:
            val = float(entry_var.get())
            val = max(vmin, min(vmax, val))
            # Round to step
            if step:
                val = round(val / step) * step
            var.set(val)
            entry_var.set(f"{val:.0f}" if step >= 1 else f"{val:.1f}")
            self._sync()
        except ValueError:
            # Restore from slider
            entry_var.set(f"{var.get():.1f}")

    def _check(self, sf, row, label: str, key: str, value: bool):
        var = tk.BooleanVar(value=value)
        self._vars[key] = var
        ttk.Checkbutton(sf.inner, text=label, variable=var,
                        command=lambda: self._sync()).grid(
            row=row, column=0, columnspan=3, sticky="w", padx=8, pady=3)

    def _hint(self, sf, row, text: str):
        """Place a subdued hint/warning below the previous control."""
        tk.Label(sf.inner, text=text, bg="#1e1e2e",
                fg="#888888", font=("Microsoft YaHei", 8),
                wraplength=320, justify="left").grid(
            row=row, column=0, columnspan=3, sticky="w", padx=24, pady=(0, 4))

    def _combo(self, sf, row, label: str, key: str, value, options: list,
               labels: dict | None = None, width: int = 14):
        """Add a combobox. If labels dict provided, shows labels but stores keys.

        labels maps key → display_text. The combobox displays the label text
        but the stored variable holds the key.
        """
        ttk.Label(sf.inner, text=label, background="#1e1e2e",
                  foreground="#cccccc").grid(row=row, column=0, sticky="w", padx=8, pady=2)
        # ★ Always store the raw key in var — display label is set separately
        # via cb.set().  This prevents display labels from leaking into config
        # when the user saves without changing the combo.
        str_value = str(value)
        str_options = [str(o) for o in options]
        # Sanitize: if the config value is corrupted (e.g. a display label
        # leaked into config from a previous bug), reset to first valid option.
        if str_value not in str_options:
            str_value = str_options[0]
        var = tk.StringVar(value=str_value)
        self._vars[key] = var
        display_options = [labels.get(str(o), str(o)) for o in options] if labels else options
        cb = ttk.Combobox(sf.inner, textvariable=var, values=display_options,
                          state="readonly", width=width)
        cb.grid(row=row, column=1, columnspan=2, sticky="w", padx=4)
        if labels:
            # Show the display label without changing the stored key
            cb.set(labels.get(str_value, str_value))
            # On selection: map display label → internal key
            cb.bind("<<ComboboxSelected>>",
                    lambda e: self._on_combo_label_select(key, options, labels))
        else:
            cb.bind("<<ComboboxSelected>>", lambda e: self._sync())

    def _on_combo_label_select(self, key: str, options: list, labels: dict):
        """Called when a labeled combobox selection changes — map display→key."""
        var = self._vars.get(key)
        if var is None:
            return
        display = var.get()
        # Reverse lookup: find which key maps to this display text
        for opt in options:
            if labels.get(str(opt), str(opt)) == display:
                var.set(str(opt))
                break
        self._sync()

    def _entry(self, sf, row, label: str, key: str, value: str, width: int = 12):
        ttk.Label(sf.inner, text=label, background="#1e1e2e",
                  foreground="#cccccc").grid(row=row, column=0, sticky="w", padx=8, pady=2)
        var = tk.StringVar(value=str(value))
        self._vars[key] = var
        entry = ttk.Entry(sf.inner, textvariable=var, width=width)
        entry.grid(row=row, column=1, columnspan=2, sticky="w", padx=4)
        entry.bind("<FocusOut>", lambda e: self._sync())
        entry.bind("<Return>", lambda e: self._sync())

    def _color(self, sf, row, label: str, key: str, value: str):
        ttk.Label(sf.inner, text=label, background="#1e1e2e",
                  foreground="#cccccc").grid(row=row, column=0, sticky="w", padx=8, pady=2)
        # Guard against empty color values that tkinter rejects
        display_color = value if (value and value.startswith("#")) else "#000000"
        var = tk.StringVar(value=value)
        self._vars[key] = var
        btn = tk.Button(sf.inner, text="  ", bg=display_color, width=5, relief="flat",
                        command=lambda: self._pick_color(var, btn))
        btn.grid(row=row, column=1, sticky="w", padx=4)

    def _font_pick(self, sf, row, label: str, eid: str, subkey: str, value: str):
        ttk.Label(sf.inner, text=label, background="#1e1e2e",
                  foreground="#cccccc").grid(row=row, column=0, sticky="w", padx=8, pady=2)
        import tkinter.font as tkfont
        avail = set(tkfont.families())
        # CJK-capable fonts first, then common Western, then all rest
        _cjk_priority = [
            "Microsoft YaHei", "Microsoft YaHei UI",
            "SimHei", "SimSun", "KaiTi", "FangSong", "STSong",
            "NSimSun", "SimKai", "YouYuan", "STCaiyun", "STXinwei",
            "Microsoft JhengHei", "MingLiU", "PMingLiU",
            "MS Gothic", "MS PGothic", "MS UI Gothic", "MS Mincho",
            "Yu Gothic", "Yu Gothic UI", "Meiryo", "Meiryo UI",
            "Malgun Gothic", "Gulim", "Batang", "Dotum",
            "PingFang SC", "PingFang TC", "PingFang HK",
            "Noto Sans CJK SC", "Noto Sans CJK TC",
            "Noto Serif CJK SC", "Noto Serif CJK TC",
        ]
        _western_priority = [
            "Segoe UI", "Arial", "Tahoma", "Verdana",
            "Times New Roman", "Courier New", "Consolas",
            "Georgia", "Comic Sans MS", "Impact", "Trebuchet MS",
            "Calibri", "Cambria", "Candara", "Constantia", "Corbel",
            "Helvetica", "Palatino Linotype", "Lucida Sans",
        ]
        # Build ordered list: priority fonts that exist, then all remaining
        # ★ Filter out "@" prefixed fonts — these are vertical-writing variants
        #   (e.g. "@等线 Light") whose file names don't match, causing font load to fail.
        seen = set()
        families = []
        for f in _cjk_priority + _western_priority:
            if f in avail and f not in seen and not f.startswith("@"):
                families.append(f)
                seen.add(f)
        # Add all remaining fonts sorted alphabetically
        rest = sorted(f for f in avail if f not in seen and not f.startswith("@"))
        families.extend(rest)

        # Strip "@" from saved value (safety net for configs saved before this fix)
        if value.startswith("@"):
            value = value[1:]
        var = tk.StringVar(value=value)
        key = self._nk(eid, subkey)
        self._vars[key] = var
        cb = ttk.Combobox(sf.inner, textvariable=var, values=families,
                          state="readonly", width=20)
        cb.grid(row=row, column=1, columnspan=2, sticky="w", padx=4)
        cb.bind("<<ComboboxSelected>>", lambda e: self._sync())

    def _pos_xy(self, sf, row, eid: str):
        """X/Y position sliders covering canvas area (up to 1500px)."""
        node = self._node(eid)
        off = node.setdefault("offset", {})
        x_key = self._nk(eid, "offset.x")
        y_key = self._nk(eid, "offset.y")

        # Read current canvas size for bounds.
        size_cfg = self._w.get("window", {}).get("size", {})
        canvas_w = int(size_cfg.get("width", 400))
        canvas_h = int(size_cfg.get("height", 550))
        # ★ Per-element safe range: element edges must stay within canvas.
        # Negative offsets are valid for right/bottom-anchored positioning.
        elem_w = int(float(node.get("size", 100)))
        elem_h = int(float(node.get("size", 100)))
        min_x = float(elem_w - canvas_w)
        max_x = float(canvas_w - elem_w)
        min_y = float(elem_h - canvas_h)
        max_y = float(canvas_h - elem_h)

        # X slider with entry (DoubleVar is required by ttk.Scale)
        ttk.Label(sf.inner, text="水平位置 X:", background="#1e1e2e",
                  foreground="#cccccc").grid(row=row, column=0, sticky="w", padx=8, pady=1)
        x_var = tk.DoubleVar(value=off.get("x", 0))
        self._vars[x_key] = x_var
        x_scale = ttk.Scale(sf.inner, from_=min_x, to=max_x, variable=x_var, length=180)
        x_scale.grid(row=row, column=1, sticky="w", padx=2)
        x_scale.bind("<ButtonRelease-1>", lambda e: self._sync())
        x_entry_var = tk.StringVar(value=f"{off.get('x', 0):.1f}")
        x_entry = ttk.Entry(sf.inner, textvariable=x_entry_var, width=6)
        x_entry.grid(row=row, column=2, sticky="w", padx=2)
        x_entry.bind("<Return>", lambda e: self._on_pos_entry(x_key, x_var, x_entry_var, min_x, max_x))
        x_entry.bind("<FocusOut>", lambda e: self._on_pos_entry(x_key, x_var, x_entry_var, min_x, max_x))
        def _x_sync(*a):
            x_entry_var.set(f"{x_var.get():.1f}")
        x_var.trace_add("write", _x_sync)
        x_lbl = ttk.Label(sf.inner, text=f"← ({off.get('x', 0)})")
        x_lbl.grid(row=row, column=3, sticky="w", padx=4)

        # Y slider with entry
        ttk.Label(sf.inner, text="垂直位置 Y:", background="#1e1e2e",
                  foreground="#cccccc").grid(row=row + 1, column=0, sticky="w", padx=8, pady=1)
        y_var = tk.DoubleVar(value=off.get("y", 0))
        self._vars[y_key] = y_var
        y_scale = ttk.Scale(sf.inner, from_=min_y, to=max_y, variable=y_var, length=180)
        y_scale.grid(row=row + 1, column=1, sticky="w", padx=2)
        y_scale.bind("<ButtonRelease-1>", lambda e: self._sync())
        y_entry_var = tk.StringVar(value=f"{off.get('y', 0):.1f}")
        y_entry = ttk.Entry(sf.inner, textvariable=y_entry_var, width=6)
        y_entry.grid(row=row + 1, column=2, sticky="w", padx=2)
        y_entry.bind("<Return>", lambda e: self._on_pos_entry(y_key, y_var, y_entry_var, min_y, max_y))
        y_entry.bind("<FocusOut>", lambda e: self._on_pos_entry(y_key, y_var, y_entry_var, min_y, max_y))
        def _y_sync(*a):
            y_entry_var.set(f"{y_var.get():.1f}")
        y_var.trace_add("write", _y_sync)
        y_lbl = ttk.Label(sf.inner, text=f"← ({off.get('y', 0)})")
        y_lbl.grid(row=row + 1, column=3, sticky="w", padx=4)

    def _on_pos_entry(self, key, var, entry_var, vmin, vmax):
        """User typed position value — validate and apply (1 decimal precision)."""
        try:
            val = float(entry_var.get())
            val = round(max(vmin, min(vmax, val)), 1)
            var.set(val)
            entry_var.set(f"{val:.1f}")
            self._sync()
        except ValueError:
            entry_var.set(f"{var.get():.1f}")

    def _clamp_positions(self) -> None:
        """Clamp all element offsets so no element exceeds canvas bounds.

        Called during _sync() after all vars are written to config.
        Ensures element left/top/right/bottom edges stay within the canvas,
        accounting for each element's configured size.
        """
        size_cfg = self._w.get("window", {}).get("size", {})
        canvas_w = int(size_cfg.get("width", 400))
        canvas_h = int(size_cfg.get("height", 550))

        for node in self._w.get("scene", {}).get("nodes", []):
            off = node.get("offset", {})
            if not off:
                continue
            eid = node.get("id", "unknown")
            # Estimate element size — most nodes have a "size" key or inherit defaults
            elem_w = int(float(node.get("size", 100)))
            elem_h = int(float(node.get("size", 100)))

            x_key = self._nk(eid, "offset.x")
            y_key = self._nk(eid, "offset.y")
            x_var = self._vars.get(x_key)
            y_var = self._vars.get(y_key)

            # ★ Clamp offset so the element stays fully within canvas bounds.
            # Offsets can be negative (right/bottom-anchored elements) — only
            # clamp the edges, not the sign.
            #   Left anchor  (anchor=0):       x ∈ [0, canvas_w - elem_w]
            #   Right anchor (anchor=canvas_w): x ∈ [elem_w - canvas_w, 0]
            # Union of both anchor extremes:    x ∈ [elem_w - canvas_w, canvas_w - elem_w]
            min_x = float(elem_w - canvas_w)
            max_x = float(canvas_w - elem_w)
            min_y = float(elem_h - canvas_h)
            max_y = float(canvas_h - elem_h)

            if "x" in off:
                old_x = off["x"]
                new_x = round(max(min_x, min(max_x, float(old_x))), 1)
                if new_x != old_x:
                    off["x"] = new_x
                    if x_var:
                        x_var.set(new_x)
                    _set_log.info(f"clamped {eid}.x: {old_x} → {new_x} "
                                  f"(bounds=[{min_x:.0f}, {max_x:.0f}])")

            if "y" in off:
                old_y = off["y"]
                new_y = round(max(min_y, min(max_y, float(old_y))), 1)
                if new_y != old_y:
                    off["y"] = new_y
                    if y_var:
                        y_var.set(new_y)
                    _set_log.info(f"clamped {eid}.y: {old_y} → {new_y} "
                                  f"(bounds=[{min_y:.0f}, {max_y:.0f}])")

    # ================================================================
    # Color picker (guarded — single instance)
    # ================================================================

    def _pick_color(self, var: tk.StringVar, btn: tk.Button) -> None:
        """Open color picker — only one at a time."""
        if self._color_picker_open:
            return
        self._color_picker_open = True
        try:
            color = colorchooser.askcolor(color=var.get(), title="选择颜色",
                                         parent=self.window)
            if color and color[1]:
                var.set(color[1])
                btn.configure(bg=color[1])
                self._sync()
        finally:
            self._color_picker_open = False

    # ================================================================
    # Sync & Save
    # ================================================================

    def _sync(self) -> None:
        """Read all vars → write to config → apply to overlay."""
        for key_path, var in self._vars.items():
            value = var.get()
            if key_path.startswith("_node."):
                # Node-level key: _node.cover.spin.speed → scene.nodes[cover].spin.speed
                parts = key_path.split(".", 2)  # ['_node', 'cover', 'spin.speed']
                if len(parts) >= 3:
                    eid = parts[1]
                    sub = parts[2]
                    node = self._node(eid)
                    self._set_deep(node, sub, value)
            else:
                self._set_deep(self._w, key_path, value)

        # ★ Clamp all element positions to canvas bounds.
        # Element offset + size must not exceed canvas width/height.
        self._clamp_positions()

        if self.on_apply and self.window:
            self.window.after(10, lambda: self.on_apply(deepcopy(self._w)))

    def _set_deep(self, d: dict, path: str, value) -> None:
        """Set a nested dict value by dot-separated path."""
        keys = path.split(".")
        for k in keys[:-1]:
            if k.isdigit():
                k = int(k)
            if isinstance(d, dict) and k not in d:
                d[k] = {}
            if isinstance(d, list):
                d = d[int(k)]
            else:
                d = d.setdefault(k, {})
        last = keys[-1]
        if last.isdigit() and isinstance(d, list):
            d[int(last)] = value
        else:
            d[last] = value

    def _get_deep(self, d: dict, path: str):
        """Read a nested dict value by dot-separated path. Returns None if missing."""
        keys = path.split(".")
        cur = d
        for k in keys:
            if isinstance(cur, dict):
                if k not in cur:
                    return None
                cur = cur[k]
            elif isinstance(cur, list) and k.isdigit():
                idx = int(k)
                if idx >= len(cur):
                    return None
                cur = cur[idx]
            else:
                return None
        return cur

    def refresh_from_config(self) -> None:
        """Refresh all UI variables from the backing config dict.

        Called when config changes externally (e.g., tray menu toggles)
        so the settings page stays in sync bidirectionally.
        """
        _refreshed = []
        for key_path, var in list(self._vars.items()):
            try:
                if key_path.startswith("_node."):
                    parts = key_path.split(".", 2)
                    if len(parts) >= 3:
                        eid = parts[1]
                        sub = parts[2]
                        node = self._node(eid)
                        value = self._get_deep(node, sub)
                else:
                    # Read from LIVE config (parent.config), not self._w
                    # (which is a deepcopy frozen at init time). Tray toggles
                    # modify parent.config directly; reading self._w would
                    # always return stale values.
                    value = self._get_deep(self.parent.config, key_path)
                if value is not None:
                    # Preserve type: BooleanVar expects bool, StringVar expects str
                    if isinstance(var, tk.BooleanVar):
                        var.set(bool(value))
                        _refreshed.append(f"{key_path}={bool(value)}")
                    elif isinstance(var, tk.StringVar):
                        var.set(str(value))
                    elif isinstance(var, tk.IntVar):
                        var.set(int(value))
                    elif isinstance(var, tk.DoubleVar):
                        var.set(float(value))
            except Exception:
                pass
        if _refreshed:
            from core.logs import get_logger
            get_logger("settings").debug(f"refresh_from_config: {', '.join(_refreshed)}")

    def _sync_and_save(self) -> None:
        """Full sync + persist to disk."""
        self._sync()
        save_config(self._w)

    # ---- 4 buttons (matching original behavior) ----

    def _check_top_source_missing(self) -> str | None:
        """Return warning text if top-priority source has missing capabilities."""
        priority = self._w.get("behavior", {}).get("detection_priority", [])
        if not priority:
            return None
        top = priority[0]
        info = self._PLAYER_INFO.get(top)
        if not info:
            return None
        missing = []
        if not info.get("lyrics"):
            missing.append("歌词")
        if not info.get("progress"):
            missing.append("实时播放进度（进度条/时长）")
        if not info.get("cover"):
            missing.append("封面")
        if not missing:
            return None
        name = info["name"]
        lines = [f"「{name}」不支持："]
        for m in missing:
            lines.append(f"  ❌ {m}")
        lines.append("\n是否自动关闭相关显示选项？")
        return "\n".join(lines)

    def _apply_missing_disable(self) -> None:
        """Disable display options matching missing capabilities of top source."""
        priority = self._w.get("behavior", {}).get("detection_priority", [])
        if not priority:
            return
        info = self._PLAYER_INFO.get(priority[0])
        if not info:
            return
        # Save pre-disable state for undo
        saved = {}
        if not info.get("progress"):
            pg = self._node("progress")
            if pg:
                saved["progress.mode"] = pg.get("mode", "bar")
                saved["progress.time_text.visible"] = pg.get("time_text", {}).get("visible", True)
                pg["mode"] = "off"
                pg.setdefault("time_text", {})["visible"] = False
                # Also update tkinter Vars so _sync() doesn't overwrite
                self._set_var(self._nk("progress", "mode"), "off")
                self._set_var(self._nk("progress", "time_text.visible"), False)
            pt = self._node("progress_time")
            if pt:
                saved["progress_time.visible"] = pt.get("visible", True)
                pt["visible"] = False
                self._set_var(self._nk("progress_time", "visible"), False)
        if not info.get("cover"):
            cv = self._node("cover")
            if cv:
                saved["cover.visible"] = cv.get("visible", True)
                cv["visible"] = False
                self._set_var(self._nk("cover", "visible"), False)
        if not info.get("lyrics"):
            ly = self._node("lyrics")
            if ly:
                saved["lyrics.visible"] = ly.get("visible", True)
                ly["visible"] = False
                self._set_var(self._nk("lyrics", "visible"), False)
        if saved:
            self._pre_disable_state = saved
            self._show_restore_button()
        # Save immediately after modifying config, bypassing _sync() which
        # would overwrite our changes with stale tkinter Var values.
        from config import save_config
        save_config(self._w)
        if self.on_apply and self.window:
            self.window.after(10, lambda: self.on_apply(deepcopy(self._w)))

    def _set_var(self, key: str, value) -> None:
        """Update a tkinter Var without triggering _sync."""
        var = self._vars.get(key)
        if var is None:
            return
        if isinstance(var, tk.BooleanVar):
            var.set(bool(value))
        elif isinstance(var, tk.StringVar):
            var.set(str(value))

    def _restore_disabled(self) -> None:
        """Restore display options that were auto-disabled by missing-cap check."""
        saved = getattr(self, '_pre_disable_state', None)
        if not saved:
            return
        for key, value in saved.items():
            if key == "progress.mode":
                pg = self._node("progress")
                if pg:
                    pg["mode"] = value
                    self._set_var(self._nk("progress", "mode"), value)
            elif key == "progress.time_text.visible":
                pg = self._node("progress")
                if pg:
                    pg.setdefault("time_text", {})["visible"] = value
                    self._set_var(self._nk("progress", "time_text.visible"), value)
            elif key == "progress_time.visible":
                pt = self._node("progress_time")
                if pt:
                    pt["visible"] = value
                    self._set_var(self._nk("progress_time", "visible"), value)
            elif key == "cover.visible":
                cv = self._node("cover")
                if cv:
                    cv["visible"] = value
                    self._set_var(self._nk("cover", "visible"), value)
            elif key == "lyrics.visible":
                ly = self._node("lyrics")
                if ly:
                    ly["visible"] = value
                    self._set_var(self._nk("lyrics", "visible"), value)
        self._pre_disable_state = None
        self._hide_restore_button()
        # Save directly, bypass _sync() to prevent stale Vars overwriting
        from config import save_config
        save_config(self._w)
        if self.on_apply and self.window:
            self.window.after(10, lambda: self.on_apply(deepcopy(self._w)))
        self._st.configure(text="已恢复显示选项", foreground="#44cc88")

    def _show_restore_button(self) -> None:
        btn = getattr(self, '_restore_btn', None)
        if btn is None:
            return
        btn.pack(side="left", padx=4)
        if getattr(self, '_restore_label', None):
            self._restore_label.pack(side="left", padx=4)

    def _check_and_apply_priority(self) -> None:
        """Apply priority and prompt about missing capabilities."""
        warning = self._check_top_source_missing()
        if warning:
            suppress = self._w.get("behavior", {}).get(
                "suppress_missing_warnings", False)
            # Only prompt once per settings window lifetime
            already = getattr(self, '_priority_popup_shown', False)
            if not suppress and not already and self.window and self.window.winfo_exists():
                self._priority_popup_shown = True
                import tkinter.messagebox as tkmb
                want = tkmb.askyesno("功能缺失提醒",
                                    warning + "\n\n💡 设置 → 来源 → 不再提示 可永久关闭",
                                    parent=self.window)
                if want:
                    self._apply_missing_disable()
                    self._st.configure(text="已应用 · 相关显示已关闭", foreground="#44cc88")
                    return
            elif suppress:
                self._apply_missing_disable()
                self._st.configure(text="已应用 · 相关显示已关闭", foreground="#44cc88")
                return
        else:
            if getattr(self, '_pre_disable_state', None):
                self._restore_disabled()
        self._sync_and_save()
        self._st.configure(text="已应用 · 可继续调整", foreground="#44cc88")

    def _hide_restore_button(self) -> None:
        btn = getattr(self, '_restore_btn', None)
        if btn:
            btn.pack_forget()
        lbl = getattr(self, '_restore_label', None)
        if lbl:
            lbl.pack_forget()

    def _apply(self) -> None:
        """应用 — sync to overlay + save, keep window open."""
        self._check_and_apply_priority()

    def _confirm(self) -> None:
        """确认 — apply + close."""
        self._apply()
        self._on_close()

    def _reset(self) -> None:
        """恢复默认 — load defaults, apply, close, reopen."""
        from config import DEFAULT_CONFIG
        self._w = deepcopy(DEFAULT_CONFIG)
        self._vars = {}
        self._on_close()
        self._create()  # Reopen with defaults

    def _do_save_theme(self, name: str) -> None:
        if not name.strip():
            return
        self._sync()
        save_theme(name.strip(), self._w)
        self._rebuild()

    def _do_load_theme(self, name: str) -> None:
        self._w = load_theme(name, self._w)
        # Apply position from theme immediately (not via _sync which skips position)
        if self.parent and hasattr(self.parent, 'engine'):
            pos_cfg = self._w.get("window", {}).get("position", {})
            engine = self.parent.engine
            new_x = int(float(pos_cfg.get("x", -1)))
            new_y = int(float(pos_cfg.get("y", -1)))
            if new_x >= 0 and new_y >= 0:
                if hasattr(engine, '_window') and engine._window:
                    engine._window.set_location(new_x, new_y)
                elif hasattr(engine, 'root') and engine.root:
                    engine.root.geometry(f"+{new_x}+{new_y}")
        self._rebuild()
        self._sync()

    def _do_rename_theme(self, old_name: str) -> None:
        from tkinter import simpledialog
        new_name = simpledialog.askstring(
            "重命名主题", f"为 '{old_name}' 输入新名称:",
            parent=self.window, initialvalue=old_name)
        if new_name and new_name.strip() and new_name.strip() != old_name:
            if rename_theme(old_name, new_name.strip()):
                self._rebuild()
            else:
                messagebox.showwarning("重命名失败",
                                       f"无法重命名 '{old_name}' → '{new_name.strip()}'（名称已存在或文件不存在）",
                                       parent=self.window)

    def _do_delete_theme(self, name: str) -> None:
        if messagebox.askyesno("删除主题", f"确定删除主题 '{name}'？"):
            delete_theme(name)
            self._rebuild()

    def _rebuild(self) -> None:
        """Rebuild all settings pages."""
        self._clear_tab_shell()
        self._vars.clear()
        self._build_tabs()

    def _on_close(self) -> None:
        if getattr(self, "_closing", False):
            return
        self._closing = True
        # Hide synchronously so the close feels immediate; freeing the large
        # settings widget tree happens once Tk returns to its idle loop.
        closing_window = self.window
        try:
            closing_window.withdraw()
        except Exception:
            pass
        closing_window.after_idle(lambda: self._finish_close(closing_window))

    def _finish_close(self, closing_window) -> None:
        if self.on_close:
            self.on_close()
        try:
            closing_window.destroy()
        except Exception:
            pass
