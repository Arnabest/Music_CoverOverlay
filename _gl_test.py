import os, sys, datetime
log_path = os.path.join(os.environ.get("TEMP", "."), "qqoverlay_ng.log")
def trace(msg):
    ts = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(f"[{ts}] [GL Test] {msg}\n")

trace("step1: importing pyglet")
import pyglet
trace(f"step2: pyglet {pyglet.version}")

trace("step3: creating config")
gl_config = pyglet.gl.Config(double_buffer=False, alpha_size=8, depth_size=0, stencil_size=0, sample_buffers=0)
trace(f"step4: config OK")

trace("step5: creating window")
w = pyglet.window.Window(width=400, height=300, caption="Test", style=pyglet.window.Window.WINDOW_STYLE_BORDERLESS, config=gl_config, vsync=False, resizable=False, visible=True)
trace(f"step6: window OK hwnd={w._hwnd}")

def on_draw():
    w.close()
    pyglet.app.exit()
w.on_draw = on_draw
trace("step7: running event loop")
pyglet.app.run(interval=1/1000)
trace("step8: DONE")