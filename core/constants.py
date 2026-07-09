"""Centralized constants for QQMusicOverlay NG.

All tunable values are defined here. Import from this module instead of
hardcoding numbers throughout the codebase.
"""
import os

# ============================================================
# Paths
# ============================================================
TEMP_DIR = os.environ.get("TEMP", ".")
LOG_BASE_DIR = TEMP_DIR
HEARTBEAT_FILE = os.path.join(TEMP_DIR, "qqoverlay_heartbeat.txt")

# ============================================================
# SMTC Engine
# ============================================================
SMTC_POLL_INTERVAL = 0.5       # seconds between SMTC polls
SMTC_JOIN_TIMEOUT = 2.0        # seconds to wait for daemon thread on stop
SMTC_ENGINE_PULSE_INTERVAL = 120  # polls between pulse heartbeat logs

# ============================================================
# Reader
# ============================================================
READER_THROTTLE = 0.1          # seconds between reader.update() calls
PLAYING_CACHE_TTL = 0.5        # seconds before re-checking is_playing
WINDOW_CACHE_TTL = 10.0        # seconds before re-enumerating windows
WEB_API_TIMEOUT = 5.0          # seconds for HTTP requests
SEARCH_THREAD_TIMEOUT = 6.0    # seconds for parallel search thread join
COVER_READY_WAIT = 1.5         # seconds SMTC cover wait window
LOG_RATE_LIMIT = 30.0          # seconds between rate-limited diagnostic logs

# Cover hash matching thresholds
HASH_DIST_16 = 8               # 16x16 perceptual hash max distance
HASH_DIST_8 = 3                # 8x8 perceptual hash max distance
FILENAME_SCORE_MIN = 3         # minimum artist+song filename score

# ============================================================
# Render — GL Engine
# ============================================================
FPS_DEFAULT = 60               # default target FPS
FPS_CAP = 120                  # absolute FPS ceiling for draw scheduling
GL_UPDATE_HEARTBEAT = 3600     # frames between _scheduled_update heartbeat logs
GL_DRAW_HEARTBEAT = 600        # frames between _on_draw heartbeat logs
GL_FPS_LOG_INTERVAL = 2.0      # seconds between FPS stat dumps

TK_READY_TIMEOUT = 3.0         # seconds to wait for tk thread ready
CRASH_RECOVERY_SLEEP = 0.5     # seconds sleep before restarting pyglet event loop
CRASH_MAX_RESTARTS = 5         # max restarts in crash window before escalating
CRASH_WINDOW = 10.0            # seconds for crash rate window

# Alpha
ALPHA_MIN = 0.0
ALPHA_MAX = 1.0
ALPHA_FLOOR = 0.05             # minimum allowed via scroll wheel
GLOBAL_ALPHA_MIN = 1           # SourceConstantAlpha minimum (1/255)
GLOBAL_ALPHA_MAX = 255         # SourceConstantAlpha maximum

# Cache limits
TEXT_CACHE_MAX = 64            # compositor text image cache entries
TEXT_CACHE_EVICT = 16          # entries to evict when cache overflows
ARC_CACHE_MAX = 50             # painter arc texture cache entries
ARC_CACHE_EVICT = 10           # entries to evict when arc cache overflows
SS_THUMB_CACHE_MAX = 8         # supersample thumbnail cache entries

# ============================================================
# Render — Scene / Layout
# ============================================================
LAYOUT_MAX_ITERATIONS = 10     # max layout resolution passes

# ============================================================
# Animation
# ============================================================
FADE_IN_DEFAULT_MS = 800       # default fade-in duration
FADE_OUT_DEFAULT_MS = 800      # default fade-out duration
STAY_SECONDS_DEFAULT = 8       # default auto-hide delay
SONG_END_FADE_MS = 550         # song-end fade-out duration
AUTO_HIDE_WAIT = 0.3           # post-fade wait before hiding
SONG_END_WAIT = 0.5            # post-fade wait after song end
PROGRESS_FADE_MS = 500         # progress bar element fade-in
SONG_CHANGE_BLANK_MIN = 0.15   # minimum blank pause between songs
COVER_WAIT_TIMEOUT = 3.0       # max wait for cover in appear animation

# ============================================================
# Progress Tracker
# ============================================================
WALL_CLOCK_SMTC_DEAD = 5.0     # seconds before declaring SMTC inactive
SMTC_CYCLE_MIN = 5             # minimum SMTC cycle length to consider
DURATION_MIN = 60              # minimum duration to accept from any source
DURATION_LEARNED_MIN = 120     # minimum learned duration to use
DURATION_EXTRAPOLATE_FACTOR = 1.15  # position × factor for extrapolation
DURATION_LEARNED_FACTOR = 1.10      # learned_dur × factor for buffer
LONG_SONG_THRESHOLD = 360      # seconds above which SMTC sanity check is relaxed
DURATION_SANITY_PCT = 1.05     # position > duration × pct → reject SMTC duration
SNAPSHOT_SAVE_INTERVAL = 2     # seconds between snapshot saves

# ============================================================
# Window / Tray
# ============================================================
TRAY_STARTUP_DELAY = 0.3       # seconds after tray.start() before continuing
PROCESS_KILL_DELAY = 0.5       # seconds after killing stale processes
WATCHDOG_INTERVAL = 2.0        # seconds between watchdog heartbeats
WATCHDOG_STALE = 10.0          # seconds before watchdog considers process dead
EXIT_WAIT = 0.5                # seconds to wait after WM_CLOSE before force kill

# ============================================================
# Lyrics
# ============================================================
LRC_CENTISECOND_ROUND = 100    # ms rounding for LRC timestamp matching
LRC_FUZZY_TOLERANCE = 800      # ms tolerance for fuzzy merge
LRC_TIGHT_TOLERANCE = 250      # ms tolerance for calibrated tight merge
CALIBRATION_CONFIDENCE = 0.35  # minimum confidence for calibration

# ============================================================
# Config defaults
# ============================================================
DEFAULT_WINDOW_WIDTH = 400
DEFAULT_WINDOW_HEIGHT = 550
DEFAULT_WINDOW_OPACITY = 0.85
DEFAULT_BG_COLOR = "#1a1a2e"
DEFAULT_BG_OPACITY = 1.0
DEFAULT_RENDERER = "opengl"
