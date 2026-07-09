"""Declarative configuration schema for QQMusic Overlay NG.

All settings are organized in a nested structure with validation.
Settings window auto-generates from this schema — no manual widget code needed.
"""
import json
import os
import shutil
from copy import deepcopy
from typing import Any

APP_DIR = os.path.dirname(os.path.abspath(__file__))
# ★ User data goes to %APPDATA%, not the app install directory.
# Survives reinstalls, doesn't mix with PyInstaller bundled files.
DATA_DIR = os.path.join(os.environ.get("APPDATA", ""), "QQMusicOverlayNG")
os.makedirs(DATA_DIR, exist_ok=True)
# ★ Migrate old data from app directory to APPDATA on first run
for _f in ("overlay_config.json", ".learned_durations.json",
           ".learned_smtc_features.json", ".wall_snapshots.json",
           ".cover_preferences.json"):
    _old = os.path.join(APP_DIR, _f)
    _new = os.path.join(DATA_DIR, _f)
    if os.path.exists(_old) and not os.path.exists(_new):
        shutil.copy2(_old, _new)
CONFIG_DIR = APP_DIR  # backward compat
CONFIG_FILE = os.path.join(DATA_DIR, "overlay_config.json")
THEMES_DIR = os.path.join(APP_DIR, "assets", "themes")
FONTS_DIR = os.path.join(APP_DIR, "assets", "fonts")

# ============================================================
# Default config — nested, declarative, self-documenting
# ============================================================

DEFAULT_CONFIG: dict[str, Any] = {
    # ---- Window ----
    "window": {
        "position": {"x": 100, "y": 100},
        "size": {"width": 400, "height": 550},
        "opacity": 0.85,             # 0.05 ~ 1.0
        "always_on_top": True,
        "click_through": False,
        "scale": 1.0,                # 0.3 ~ 3.0, window size multiplier
        "background": {              # Window background (behind all elements)
            "color": "#1a1a2e",
            "opacity": 1.0,          # 0.0 ~ 1.0
        },
    },

    # ---- Animation ----
    "animation": {
        "fade_in_ms": 800,
        "fade_out_ms": 800,
        "stay_seconds": 8,           # Auto-hide after this many seconds
        "stay_forever": True,       # Default: always visible (no auto-hide)
        "fps": 60,                   # Render FPS — smooth animation
        "continuous_fps_mode": "eco", # eco | full — cover spin FPS strategy
                                       #   eco:  cap at continuous_fps_cap (power saving)
                                       #   full: match target fps (old behavior)
        "continuous_fps_cap": 45,    # FPS cap for eco mode (30-60), default 45
        "fullscreen_eco_mode": "pause", # pause | 30fps | 15fps — behavior when
                                        #   fullscreen app covers the overlay
                                        #   (only when always_on_top is OFF):
                                        #     pause: stop cover rotation entirely
                                        #     30fps: cap rotation at 30fps
                                        #     15fps: cap rotation at 15fps
        "loading_timeout": 8,        # Max seconds to wait for cover art
        "renderer": "pil",           # pil | opengl — rendering backend
    },

    # ---- Scene (visual layout) ----
    "scene": {
        "nodes": [
            # Cover art
            {
                "id": "cover",
                "type": "cover",
                "visible": True,
                "anchor": {"self": "top", "parent": "top"},
                "offset": {"x": 0, "y": 20},
                "size": 180,
                "shape": "circle",              # circle | roundrect | square
                "border_radius": 20,
                "feather": 5,                   # 0-30, edge softness
                "supersample": "2X",            # 2X|4X|6X, render quality multiplier
                "stroke": {
                    "color": "",                # hex color, "" = disabled
                    "width": 0,                 # px, 0 = disabled
                },
                "spin": {
                    "enabled": True,
                    "speed": 30,                # degrees/sec
                    "direction": "cw",          # cw | ccw
                    "spin_duration": 2.5,       # sec to reach full speed from stop
                    "stop_duration": 1.5,       # sec to come to complete stop
                    "pause_spin": True,         # pause rotation on playback pause
                    "wait_spin_on_change": False,
                    # True: fade-out waits for cover to stop spinning
                    # False: cover stops instantly, fade-out starts immediately
                },
                "effects": {
                    "cd_groove": {"enabled": False, "intensity": 0.5, "frequency": 80},
                    "motion_blur": {"enabled": False, "intensity": 0.5},
                    "specular": {"enabled": False, "intensity": 0.6},
                },
            },
            # Song title
            {
                "id": "title",
                "type": "text",
                "visible": True,
                "anchor": {"self": "top", "parent": "top"},
                "offset": {"x": 0, "y": 212},
                "source": "song_name",
                "font": {"family": "Microsoft YaHei", "size": 18, "bold": True},
                "color": "#ffffff",
                "effects": {
                    "outline": {"color": "#232323", "width": 1},
                },
                "max_width": 350,
            },
            # Artist
            {
                "id": "artist",
                "type": "text",
                "visible": True,
                "anchor": {"self": "top", "parent": "top"},
                "offset": {"x": 0, "y": 268},
                "source": "artist_name",
                "font": {"family": "Microsoft YaHei", "size": 14, "bold": False},
                "color": "#aaccff",
                "max_width": 350,
            },
            # Synced lyrics
            {
                "id": "lyrics",
                "type": "synced_lyrics",
                "visible": True,
                "anchor": {"self": "top", "parent": "top"},
                "offset": {"x": 0, "y": 320},
                "font": {"family": "Microsoft YaHei", "size": 14, "bold": False},
                "color": "#cccccc",
                "highlight_color": "#88ccff",
                "max_lines": 1,
                "max_width": 350,
                "bilingual": True,
                "alignment": "center",       # left | center | right
                "time_offset_ms": 0,
            },
            # Progress bar
            {
                "id": "progress",
                "type": "progress_bar",
                "visible": True,
                "anchor": {"self": "top", "parent": "top"},
                "offset": {"x": 0, "y": 390},
                "mode": "bar",                  # off | bar | arc | ring | roundrect
                "color": "#44cc88",
                "background_color": "#333355",
                "width": 4,
                # Bar mode settings (isolated from arc/ring/roundrect)
                "bar": {
                    "length_ratio": 0.7,        # fraction of available width
                    "direction": "lr",          # lr | rl
                    "start_position": "left",   # left | right
                },
                # Arc/Ring/Roundrect mode settings
                "arc": {
                    "start_position": "top",    # top | right | bottom | left
                    "direction": "cw",          # cw | ccw
                    "cap_style": "round",       # round | flat
                    "supersample": "2X",        # 2X|4X|6X, progress arc/ring AA quality
                    "position": "edge",         # edge | inside | outside — relative to cover
                    "distance": 0,              # pixels offset from cover edge
                },
                "time_text": {
                    "visible": True,
                    "position": "bottom",
                    "offset_x": 0,               # fine-tune X offset (px)
                    "offset_y": 0,               # fine-tune Y offset (px)
                    "font": {"family": "Microsoft YaHei", "size": 8, "bold": False},
                    "color": "#cccccc",
                    "outline_color": "",
                    "outline_width": 0,
                },
            },
            # Standalone progress time display
            {
                "id": "progress_time",
                "type": "progress_time",
                "visible": True,
                "anchor": {"self": "top", "parent": "top"},
                "offset": {"x": 0, "y": 426},
                "font": {"family": "Microsoft YaHei", "size": 10, "bold": False},
                "color": "#cccccc",
                "format": "elapsed_total",       # elapsed_total | elapsed | remaining
                "separator": " / ",
            },
        ],
    },

    # ---- Behavior ----
    "behavior": {
        "qqmusic_picture_dir": "",
        "refresh_interval_ms": 100,     # Reader poll interval (SMTC poll is always 500ms)
        "cover_source_index": 0,        # 0=auto, 1+=manual (index into cover_candidates)
        "cover_source_name": "",       # sticky by name — survives candidate list changes
        "current_theme": "",
        "max_log_lines": 500,          # log retention per file, 0=unlimited
        "detection_priority": ["qqmusic", "netease"],
        # Ordered list: first = highest priority. "qqmusic" | "netease"
        "lyrics_source_priority": "qq_first",
        # Lyrics source strategy:
        #   "qq_first" — QQ Music primary, NetEase fallback when QQ has no lyrics
        #   "ne_first" — NetEase primary, QQ fallback
        #   "qq_only"  — QQ Music only (no NetEase fallback)
        #   "ne_only"  — NetEase Cloud Music only
        "suppress_missing_warnings": False,
        # When True, don't show popup warnings about missing player capabilities
    },

    # ---- Debug ----
    "debug": {
        "show_fps": False,              # Real-time FPS counter overlay
    },
}

# Layout presets (override scene.nodes positions/anchors)
LAYOUT_PRESETS = {
    "default": {
        "name": "默认-居中",
        "overrides": {},  # No overrides = use default config
    },
    "left-aligned": {
        "name": "垂直-居左",
        "overrides": {
            "cover": {"anchor": {"self": "left", "parent": "top"}, "offset": {"x": 20, "y": 30}},
            "title": {"anchor": {"self": "left", "parent": "top"}, "offset": {"x": 20, "y": 242}},
            "artist": {"anchor": {"self": "left", "parent": "top"}, "offset": {"x": 20, "y": 298}},
            "lyrics": {"anchor": {"self": "left", "parent": "top"}, "offset": {"x": 20, "y": 350}},
            "progress": {"anchor": {"self": "left", "parent": "top"}, "offset": {"x": 20, "y": 420}},
            "progress_time": {"anchor": {"self": "left", "parent": "top"}, "offset": {"x": 20, "y": 456}},
        },
    },
    "horizontal-center": {
        "name": "水平-居中",
        "overrides": {
            "cover": {"anchor": {"self": "top", "parent": "left"}, "offset": {"x": 30, "y": 0}},
            "title": {"anchor": {"self": "left", "parent": "right_of cover"}, "offset": {"x": 20, "y": -40}},
            "artist": {"anchor": {"self": "left", "parent": "right_of cover"}, "offset": {"x": 20, "y": 0}},
            "lyrics": {"anchor": {"self": "left", "parent": "right_of cover"}, "offset": {"x": 20, "y": 40}},
            "progress": {"anchor": {"self": "top", "parent": "top"}, "offset": {"x": 0, "y": 420}},
            "progress_time": {"anchor": {"self": "top", "parent": "top"}, "offset": {"x": 0, "y": 456}},
        },
    },
}


# ============================================================
# Config loading / saving
# ============================================================

def load_config(path: str | None = None) -> dict:
    """Load config from disk, merge with defaults.

    Args:
        path: Optional config file path. Defaults to CONFIG_FILE.
    """
    config_file = path if path else CONFIG_FILE
    config = deepcopy(DEFAULT_CONFIG)
    if os.path.exists(config_file):
        try:
            with open(config_file, "r", encoding="utf-8") as f:
                saved = json.load(f)
            _deep_merge(config, saved)
        except (json.JSONDecodeError, IOError):
            pass
    # Migrate: ensure all default scene nodes exist (new nodes added in updates)
    _migrate_nodes(config)
    # Migrate: old detection_source (string) → detection_priority (list)
    behavior = config.setdefault("behavior", {})
    if "detection_source" in behavior:
        old = behavior.pop("detection_source")
        if old == "qqmusic":
            behavior["detection_priority"] = ["qqmusic"]
        elif old == "netease":
            behavior["detection_priority"] = ["netease"]
        # "auto" → keep default ["qqmusic", "netease"]
    return config


def _migrate_nodes(config: dict) -> None:
    """Ensure all default node IDs are present and anchors are independent (absolute)."""
    default_nodes = DEFAULT_CONFIG.get("scene", {}).get("nodes", [])
    current_nodes = config.get("scene", {}).get("nodes", [])
    existing_ids = {n.get("id") for n in current_nodes}
    for dn in default_nodes:
        if dn.get("id") not in existing_ids:
            current_nodes.append(deepcopy(dn))

    # Migrate: switch relative anchors (bottom_of X) to absolute (top)
    # so each element moves independently when its offset is changed.
    # Old relative offsets (6, 12, 20) become meaningless — reset to sensible defaults.
    _RELATIVE_TO_ABSOLUTE = {
        "title":         {"x": 0, "y": 212},
        "artist":        {"x": 0, "y": 268},
        "lyrics":        {"x": 0, "y": 320},
        "progress":      {"x": 0, "y": 390},
        "progress_time": {"x": 0, "y": 426},
    }
    for node in current_nodes:
        nid = node.get("id")
        parent = (node.get("anchor") or {}).get("parent", "")
        if parent.startswith("bottom_of ") or parent.startswith("top_of "):
            defaults = _RELATIVE_TO_ABSOLUTE.get(nid, {"x": 0, "y": 0})
            node.setdefault("anchor", {})["parent"] = "top"
            # Reset offsets to absolute defaults (old relative values are meaningless)
            node["offset"] = {"x": defaults["x"], "y": defaults["y"]}
        # Migrate: add supersample key to cover/progress nodes (added 2026-06-24)
        if node.get("id") in ("cover", "progress") and "supersample" not in node:
            node["supersample"] = "2X"
        # Migrate: progress bar mode-specific settings (added 2026-06-25)
        # Copy old flat keys into namespaced bar/arc sub-dicts if missing
        if node.get("id") == "progress":
            if "bar" not in node:
                node["bar"] = {
                    "length_ratio": node.get("length_ratio", 0.7),
                    "direction": node.get("direction", "lr"),
                    "start_position": node.get("start_position", "left"),
                }
            if "arc" not in node:
                node["arc"] = {
                    "start_position": node.get("start_position", "top"),
                    "direction": node.get("direction", "cw"),
                    "cap_style": node.get("cap", "round"),
                    "supersample": node.get("supersample", "2X"),
                }
            # Migrate cap → arc.cap_style if needed
            if "cap" in node and "cap_style" not in node.get("arc", {}):
                node.setdefault("arc", {})["cap_style"] = node["cap"]
            # Migrate: add arc.position / arc.distance if missing
            if "position" not in node.get("arc", {}):
                node.setdefault("arc", {})["position"] = "edge"
            if "distance" not in node.get("arc", {}):
                node.setdefault("arc", {})["distance"] = 0
            # ★ Fix corrupted combo values (suffix bug: "cw 顺时针" → "cw")
            for sub in ("bar", "arc"):
                sd = node.get(sub, {})
                for k in ("direction", "cap_style", "position"):
                    if k in sd and isinstance(sd[k], str) and " " in sd[k]:
                        sd[k] = sd[k].split(" ")[0]
        # ★ Fix corrupted spin direction (cover node)
        if node.get("id") == "cover" and "spin" in node:
            spin = node["spin"]
            if isinstance(spin.get("direction"), str) and " " in spin["direction"]:
                spin["direction"] = spin["direction"].split(" ")[0]
            # ★ Migrate old "acceleration" → "spin_duration" + "stop_duration"
            if "acceleration" in spin and "spin_duration" not in spin:
                spin["spin_duration"] = float(spin["acceleration"])
                spin["stop_duration"] = float(spin["acceleration"]) * 0.6
    # Migrate: add renderer key (added 2026-06-24)
    if "renderer" not in config.get("animation", {}):
        config.setdefault("animation", {})["renderer"] = "pil"


# ============================================================
# Backend detection
# ============================================================

def detect_available_renderers() -> dict[str, bool]:
    """Check which rendering backends are available on this system."""
    available = {"pil": True}  # PIL is always available

    # pillow-simd: drop-in replacement, faster CPU path
    try:
        import PIL.Image
        if hasattr(PIL.Image, 'Resampling'):
            available["pil-simd"] = True
        else:
            available["pil-simd"] = False
    except Exception:
        available["pil-simd"] = False

    # OpenGL: requires pyglet + working GL context
    try:
        import pyglet  # noqa: F401
        available["opengl"] = True
    except Exception:
        available["opengl"] = False

    return available


def save_config(config: dict) -> None:
    """Save config to disk (pretty-printed JSON)."""
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)


def apply_preset(config: dict, preset_name: str) -> dict:
    """Apply a layout preset, returning updated config."""
    preset = LAYOUT_PRESETS.get(preset_name)
    if not preset:
        return config
    nodes = config["scene"]["nodes"]
    overrides = preset["overrides"]
    for node in nodes:
        nid = node["id"]
        if nid in overrides:
            node.update(deepcopy(overrides[nid]))
    return config


# ============================================================
# Theme management
# ============================================================

THEME_IGNORE_KEYS = {"behavior.qqmusic_picture_dir", "behavior.current_theme"}


def list_themes() -> list[str]:
    """List available theme names (without .json extension)."""
    if not os.path.isdir(THEMES_DIR):
        return []
    themes = []
    for f in sorted(os.listdir(THEMES_DIR)):
        if f.lower().endswith(".json"):
            themes.append(f[:-5])
    return themes


def save_theme(name: str, config: dict) -> None:
    """Save current config as a theme."""
    os.makedirs(THEMES_DIR, exist_ok=True)
    # Strip ignored keys
    export = deepcopy(config)
    for key_path in THEME_IGNORE_KEYS:
        _del_nested(export, key_path)
    path = os.path.join(THEMES_DIR, f"{name}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(export, f, indent=2, ensure_ascii=False)


def load_theme(name: str, base_config: dict | None = None) -> dict:
    """Load a theme and merge onto base config."""
    path = os.path.join(THEMES_DIR, f"{name}.json")
    if not os.path.exists(path):
        return deepcopy(base_config or DEFAULT_CONFIG)
    with open(path, "r", encoding="utf-8") as f:
        theme = json.load(f)
    merged = deepcopy(base_config or DEFAULT_CONFIG)
    _deep_merge(merged, theme)
    return merged


def rename_theme(old_name: str, new_name: str) -> bool:
    """Rename a saved theme. Returns True on success."""
    old_path = os.path.join(THEMES_DIR, f"{old_name}.json")
    new_path = os.path.join(THEMES_DIR, f"{new_name}.json")
    if not os.path.exists(old_path):
        return False
    if os.path.exists(new_path):
        return False  # target already exists
    os.rename(old_path, new_path)
    return True


def delete_theme(name: str) -> None:
    """Delete a theme file."""
    path = os.path.join(THEMES_DIR, f"{name}.json")
    if os.path.exists(path):
        os.remove(path)


# ============================================================
# Custom font registration (unchanged from original)
# ============================================================

def list_custom_fonts() -> list[str]:
    """List .ttf/.otf files in fonts directory."""
    fonts = []
    if os.path.isdir(FONTS_DIR):
        for f in sorted(os.listdir(FONTS_DIR)):
            if f.lower().endswith((".ttf", ".otf")):
                fonts.append(os.path.join(FONTS_DIR, f))
    return fonts


def register_custom_fonts() -> int:
    """Register custom fonts via GDI AddFontResourceExW(FR_PRIVATE)."""
    import ctypes
    from ctypes import wintypes
    FR_PRIVATE = 0x10
    count = 0
    for path in list_custom_fonts():
        try:
            ctypes.windll.gdi32.AddFontResourceExW(
                wintypes.LPCWSTR(path), FR_PRIVATE, 0)
            count += 1
        except Exception:
            pass
    return count


# ============================================================
# Auto-start
# ============================================================

def is_autostart_enabled() -> bool:
    startup = os.path.join(os.environ.get("APPDATA", ""),
                           "Microsoft", "Windows", "Start Menu",
                           "Programs", "Startup", "QQMusicOverlayNG.lnk")
    return os.path.exists(startup)


def set_autostart(enabled: bool) -> None:
    startup_dir = os.path.join(os.environ.get("APPDATA", ""),
                               "Microsoft", "Windows", "Start Menu",
                               "Programs", "Startup")
    path = os.path.join(startup_dir, "QQMusicOverlayNG.lnk")
    if enabled:
        # Use the EXE if we're running from a packaged build,
        # otherwise fall back to pythonw + main.py for development.
        exe = os.path.join(CONFIG_DIR, "QQMusicOverlayNG.exe")
        if not os.path.exists(exe):
            exe = os.path.join(CONFIG_DIR, "dist", "QQMusicOverlayNG",
                              "QQMusicOverlayNG.exe")
        if os.path.exists(exe):
            import win32com.client
            shell = win32com.client.Dispatch("WScript.Shell")
            shortcut = shell.CreateShortCut(path)
            shortcut.TargetPath = exe
            shortcut.WorkingDirectory = os.path.dirname(exe)
            shortcut.Save()
        else:
            import sys
            pythonw = sys.executable.replace("python.exe", "pythonw.exe")
            import win32com.client
            shell = win32com.client.Dispatch("WScript.Shell")
            shortcut = shell.CreateShortCut(path)
            shortcut.TargetPath = pythonw
            shortcut.Arguments = os.path.join(CONFIG_DIR, "main.py")
            shortcut.WorkingDirectory = CONFIG_DIR
            shortcut.Save()
    else:
        if os.path.exists(path):
            os.remove(path)


# ============================================================
# Internal helpers
# ============================================================

def _deep_merge(base: dict, override: dict) -> None:
    """Recursively merge override into base (mutates base)."""
    for key, value in override.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _deep_merge(base[key], value)
        else:
            base[key] = deepcopy(value)


def _del_nested(d: dict, key_path: str) -> None:
    """Delete a nested key from dict (e.g., 'a.b.c')."""
    keys = key_path.split(".")
    current = d
    for key in keys[:-1]:
        if key not in current:
            return
        current = current[key]
    if keys[-1] in current:
        del current[keys[-1]]
