# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules
from PyInstaller.utils.hooks import collect_all

datas = [('assets', 'assets'), ('config.py', '.'), ('core', 'core'), ('render', 'render'), ('ui', 'ui'), ('scene', 'scene'), ('animation', 'animation')]
binaries = []
hiddenimports = ['core.reader', 'core.window_detect', 'core.web_api', 'core.smtc_engine', 'core.progress', 'core.duration', 'core.logs', 'core.lyrics', 'core.cover_prefs', 'core.cover_cache', 'core.smtc_features', 'render.engine', 'render.compositor', 'render.painter', 'render.spin_controller', 'render.shaders', 'render.gl_engine', 'scene.graph', 'scene.nodes', 'animation.timeline', 'animation.transitions', 'animation.state_machine', 'ui.window', 'ui.settings', 'ui.tray', 'tkinter', 'tkinter.ttk', 'tkinter.messagebox', 'tkinter.filedialog', 'tkinter.colorchooser', 'urllib.request', 'urllib.parse']
hiddenimports += collect_submodules('win32com')
tmp_ret = collect_all('PIL')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('pyglet')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('winrt')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Music_CoverOverlay',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['assets\\app_icon.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='Music_CoverOverlay',
)
