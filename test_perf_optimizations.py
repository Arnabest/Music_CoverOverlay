"""Verify performance optimizations — frame buffer reuse, text cache, measure_text."""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from render.compositor import Compositor
from render.text import TextRenderer, get_text_renderer
from scene.graph import SceneGraph
from scene.nodes import TextNode, SyncedLyricsNode, CoverNode, ProgressBarNode
from PIL import Image


def make_text_node(node_id, text="", source="", **extra):
    """Create a positioned, visible TextNode with default styling."""
    node = TextNode(node_id=node_id, data={
        "text": text,
        "source": source,
        "font_family": "Microsoft YaHei",
        "font_size": 14,
        "font_bold": False,
        "color": "#ffffff",
        "max_width": 350,
        **extra,
    })
    node._abs_x, node._abs_y = 50, 100
    node._abs_width, node._abs_height = 300, 40
    node.visible = True
    node.opacity = 1.0
    return node


def test_frame_buffer_reuse():
    """Frame buffer should be reused across frames of the same size."""
    config = {"window": {"background": {"color": "#1a1a2e", "opacity": 1.0}}}
    c = Compositor(config)
    scene = SceneGraph()
    scene.load_from_config({"nodes": []})

    data = {"song_name": "", "artist_name": "", "lyrics_timed": [],
            "progress_pos": 0, "progress_dur": 0}
    img1 = c.render_to_pil(scene, data, 400, 550)
    buf1 = c._frame_buf
    assert buf1 is not None, "Frame buffer should be allocated"
    assert c._frame_buf_size == (400, 550), "Buffer size should match"

    img2 = c.render_to_pil(scene, data, 400, 550)
    buf2 = c._frame_buf
    assert buf2 is buf1, "Frame buffer should be REUSED (same object)"
    assert img2 is img1, "Returned image should be the same buffer object"

    c.render_to_pil(scene, data, 600, 800)
    buf3 = c._frame_buf
    assert buf3 is not buf1, "Buffer should be reallocated on size change"
    assert c._frame_buf_size == (600, 800)

    print("  [PASS] frame_buffer_reuse")


def test_text_image_cache():
    """Text images should be cached and reused for identical text."""
    config = {"window": {"background": {"color": "#1a1a2e", "opacity": 1.0}}}
    c = Compositor(config)
    scene = SceneGraph()

    text_node = make_text_node("title", text="Waka Waka",
                               font_size=24, font_bold=True,
                               outline_color="#000000", outline_width=1)
    scene.add_node(text_node)

    data = {"song_name": "Test Song", "artist_name": "Test Artist",
            "lyrics_timed": [], "cover_image": None,
            "progress_pos": 0, "progress_dur": 0}

    cache_before = len(c._text_img_cache)
    c.render_to_pil(scene, data, 400, 550)
    cache_after = len(c._text_img_cache)
    assert cache_after > cache_before, "Should create text cache entry"

    c.render_to_pil(scene, data, 400, 550)
    assert len(c._text_img_cache) == cache_after, "Should NOT add new cache entry"

    text_node.data["text"] = "Different Song"
    c.render_to_pil(scene, data, 400, 550)
    assert len(c._text_img_cache) == cache_after + 1, "New text = new cache entry"

    text_node.data["text"] = "Waka Waka"
    c.render_to_pil(scene, data, 400, 550)
    assert len(c._text_img_cache) == cache_after + 1, "Should hit existing cache"

    print("  [PASS] text_image_cache")


def test_text_source_substitution():
    """Text from song_name/artist_name source should substitute correctly."""
    config = {"window": {"background": {"color": "#1a1a2e", "opacity": 1.0}}}
    c = Compositor(config)
    scene = SceneGraph()

    song_node = make_text_node("song_name", source="song_name", font_size=20)
    scene.add_node(song_node)

    data1 = {"song_name": "Waka Waka", "artist_name": "Shakira",
             "lyrics_timed": [], "cover_image": None,
             "progress_pos": 0, "progress_dur": 0}
    c.render_to_pil(scene, data1, 400, 550)
    assert len(c._text_img_cache) >= 1, "Should cache song name text"

    data2 = {"song_name": "Hips Don't Lie", "artist_name": "Shakira",
             "lyrics_timed": [], "cover_image": None,
             "progress_pos": 0, "progress_dur": 0}
    c.render_to_pil(scene, data2, 400, 550)
    assert len(c._text_img_cache) >= 2, "Should cache new song name"

    print("  [PASS] text_source_substitution")


def test_backplate_skip():
    """Backplate should be skipped when bg is fully opaque."""
    config = {"window": {"background": {"color": "#1a1a2e", "opacity": 1.0}}}
    c = Compositor(config)

    cover = Image.new("RGBA", (10, 10), (0, 100, 200, 255))
    c.set_data(cover_image=cover, song_name="Test", artist_name="",
               lyrics_timed=[], progress_pos=0, progress_dur=0)

    scene = SceneGraph()
    cover_node = CoverNode(node_id="cover", data={
        "size": 180, "shape": "circle", "feather": 5, "supersample": "2X",
        "spin": {"enabled": False},
    })
    cover_node._abs_x = cover_node._abs_y = 50
    cover_node._abs_width = cover_node._abs_height = 180
    cover_node.visible = True
    cover_node.opacity = 1.0
    scene.add_node(cover_node)

    calls = []
    orig = c._draw_cover_backplate
    def tracking_draw(*args, **kwargs):
        calls.append(1)
        return orig(*args, **kwargs)
    c._draw_cover_backplate = tracking_draw

    c.render_to_pil(scene, {"song_name": "Test", "artist_name": "",
                             "lyrics_timed": [], "cover_image": cover,
                             "progress_pos": 0, "progress_dur": 0}, 400, 550)
    assert len(calls) == 0, f"Backplate SKIPPED when opaque (calls={len(calls)})"

    c.bg_opacity = 0.5
    c.render_to_pil(scene, {"song_name": "Test", "artist_name": "",
                             "lyrics_timed": [], "cover_image": cover,
                             "progress_pos": 0, "progress_dur": 0}, 400, 550)
    assert len(calls) == 1, f"Backplate DRAWN when transparent (calls={len(calls)})"

    c._draw_cover_backplate = orig
    print("  [PASS] backplate_skip")


def test_profile_report():
    """get_profile_report should return timing data and reset accumulators."""
    config = {"window": {"background": {"color": "#1a1a2e", "opacity": 1.0}}}
    c = Compositor(config)
    scene = SceneGraph()
    scene.load_from_config({"nodes": []})

    data = {"song_name": "", "artist_name": "", "lyrics_timed": [],
            "cover_image": None, "progress_pos": 0, "progress_dur": 0}

    for _ in range(10):
        c.render_to_pil(scene, data, 400, 550)

    report = c.get_profile_report()
    assert report["frames"] == 10, f"Expected 10 frames, got {report['frames']}"
    assert "init_ms" in report
    assert "cover_ms" in report
    assert "text_ms" in report
    assert c._prof_frame_count == 0, "Counters should reset after report"

    print("  [PASS] profile_report")


def test_measure_text():
    """measure_text should return dimensions without GL upload."""
    tr = get_text_renderer()

    w, h = tr.measure_text("Hello World", "Microsoft YaHei", 14, False)
    assert w > 20, f"Width too small: {w}"
    assert h > 10, f"Height too small: {h}"

    w2, h2 = tr.measure_text("你好世界", "Microsoft YaHei", 14, False)
    assert w2 > 20, f"CJK width too small: {w2}"

    w3, h3 = tr.measure_text("Very long text that should wrap",
                             "Microsoft YaHei", 14, False, max_width=60)
    assert h3 > h, f"Wrapped text should be taller: {h3} vs {h}"

    tr.render_to_texture("Hello World", "Microsoft YaHei", 14, False, (255, 255, 255))
    cache_key = "Hello World|Microsoft YaHei|14|False|(255, 255, 255)|0|left"
    rw, rh = tr.get_texture_size(cache_key)
    assert abs(w - rw) <= 2, f"Width mismatch: measure={w} render={rw}"
    assert abs(h - rh) <= 4, f"Height mismatch: measure={h} render={rh}"

    print("  [PASS] measure_text")


def test_text_cache_limit():
    """Text cache should be bounded at 64 entries."""
    config = {"window": {"background": {"color": "#1a1a2e", "opacity": 1.0}}}
    c = Compositor(config)
    scene = SceneGraph()

    text_node = make_text_node("t", text="")
    scene.add_node(text_node)

    data = {"song_name": "Test Song", "artist_name": "Test Artist",
            "lyrics_timed": [], "cover_image": None,
            "progress_pos": 0, "progress_dur": 0}

    for i in range(100):
        text_node.data["text"] = f"Line {i:03d}"
        c.render_to_pil(scene, data, 400, 550)

    assert len(c._text_img_cache) <= 64, \
        f"Cache should be <=64, got {len(c._text_img_cache)}"

    print("  [PASS] text_cache_limit")


def test_lyrics_cache():
    """Lyrics text should use the text image cache."""
    config = {"window": {"background": {"color": "#1a1a2e", "opacity": 1.0}}}
    c = Compositor(config)
    scene = SceneGraph()

    lyrics_node = SyncedLyricsNode(node_id="lyrics", data={
        "font_family": "Microsoft YaHei",
        "font_size": 14,
        "color": "#cccccc",
        "max_width": 350,
    })
    lyrics_node._abs_x, lyrics_node._abs_y = 50, 200
    lyrics_node._abs_width, lyrics_node._abs_height = 300, 80
    lyrics_node.visible = True
    lyrics_node.opacity = 1.0
    scene.add_node(lyrics_node)

    data = {"song_name": "Test Song", "artist_name": "Test Artist",
            "lyrics_timed": [(0, "Line one"), (3000, "Line two"), (6000, "Line three")],
            "cover_image": None,
            "progress_pos": 1.5, "progress_dur": 10}

    c.render_to_pil(scene, data, 400, 550)
    assert len(c._text_img_cache) >= 1, "Should cache lyrics text"

    data["progress_pos"] = 4.5
    c.render_to_pil(scene, data, 400, 550)
    assert len(c._text_img_cache) >= 2, "Should cache new lyrics text"

    print("  [PASS] lyrics_cache")


if __name__ == "__main__":
    print("Performance Optimization Verification:")
    test_frame_buffer_reuse()
    test_text_image_cache()
    test_text_source_substitution()
    test_backplate_skip()
    test_profile_report()
    test_measure_text()
    test_text_cache_limit()
    test_lyrics_cache()
    print("\n  All 8 tests PASSED")
