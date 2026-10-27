"""Application version — auto-generated on each build.

Read by: settings About tab, overlay watermark, installer metadata.
Updated by: the build process before PyInstaller packaging.

Format: YYYY.MM.DD.build — e.g. "2026.06.27.1625"
"""
import datetime
import os

APP_NAME = "QQMusicOverlayNG"
APP_DISPLAY_NAME = "QQMusicOverlay NG"
APP_DESCRIPTION = "适用于QQ Music和NetEase Cloud Music的封面悬浮窗程序"

# ---- Build metadata (updated automatically before each build) ----
BUILD_VERSION = "2026.10.27.0829"    # YYYY.MM.DD.HHMM (auto-stamped)
BUILD_DISPLAY  = "v2.2.0"           # Human-readable version string
BUILD_STAGE    = "stable"            # dev | alpha | beta | stable

# ---- Auto-detect from file modification time as fallback ----
def _detect_build_time() -> str:
    """Fallback: read from this file's modification time."""
    try:
        mtime = os.path.getmtime(__file__)
        return datetime.datetime.fromtimestamp(mtime).strftime("%Y.%m.%d.%H%M")
    except Exception:
        return "unknown"


def get_version() -> str:
    """Return the full version string for display."""
    return f"QQMusicOverlay NG {BUILD_DISPLAY} (build {BUILD_VERSION})"


def get_short_version() -> str:
    """Return short version for watermark."""
    return BUILD_DISPLAY


def get_build_stamp() -> str:
    """Return build timestamp for watermark."""
    return BUILD_VERSION


def stamp_build() -> str:
    """Update BUILD_VERSION with current timestamp. Called before PyInstaller.

    Returns the new version string.
    """
    now = datetime.datetime.now()
    new_ver = now.strftime("%Y.%m.%d.%H%M")
    m = str(now.month); d = str(now.day)  # no zero-pad on Windows
    new_display = f"v{m}.{d} Beta"

    this_file = os.path.abspath(__file__)
    with open(this_file, "r", encoding="utf-8") as f:
        content = f.read()

    import re
    content = re.sub(
        r'BUILD_VERSION\s*=\s*"[^"]*"',
        f'BUILD_VERSION = "{new_ver}"',
        content,
    )
    content = re.sub(
        r'BUILD_DISPLAY\s*=\s*"[^"]*"',
        f'BUILD_DISPLAY  = "{new_display}"',
        content,
    )

    with open(this_file, "w", encoding="utf-8") as f:
        f.write(content)

    # Update global in this process
    global BUILD_VERSION, BUILD_DISPLAY
    BUILD_VERSION = new_ver
    BUILD_DISPLAY = new_display

    return new_ver
