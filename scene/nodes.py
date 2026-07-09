"""Specific node types for the scene graph.

Each node type has:
- A data schema (default values for the data dict)
- A render method signature (implemented by the render layer)
"""
from dataclasses import dataclass, field
from typing import Any
from copy import deepcopy
from scene.node import SceneNode


class CoverNode(SceneNode):
    """Album cover art with optional progress indicator."""

    def __init__(self, node_id: str = "cover", **kwargs):
        super().__init__(id=node_id, node_type="cover", **kwargs)
        self.data.setdefault("size", 180)
        self.data.setdefault("shape", "circle")       # circle | roundrect | square
        self.data.setdefault("border_radius", 20)
        self.data.setdefault("feather", 5)            # 0-30
        self.data.setdefault("supersample", "2X")     # 2X|4X|6X
        self.data.setdefault("stroke", {
            "color": "",        # hex color, "" = disabled
            "width": 0,         # px, 0 = disabled
        })
        self.data.setdefault("spin", {
            "enabled": True,
            "speed": 30,          # deg/s
            "direction": "cw",    # cw | ccw
            "acceleration": 2.5,  # seconds
            "pause_spin": True,   # pause rotation on playback pause
        })

    @property
    def size(self) -> float:
        return self.data.get("size", 180)


class TextNode(SceneNode):
    """Static or dynamic text element."""

    def __init__(self, node_id: str, **kwargs):
        super().__init__(id=node_id, node_type="text", **kwargs)
        self.data.setdefault("source", node_id)       # "song_name" | "artist_name" | static text
        self.data.setdefault("text", "")              # Static text (if source is empty)
        self.data.setdefault("font_family", "Microsoft YaHei")
        self.data.setdefault("font_size", 14)
        self.data.setdefault("font_bold", False)
        self.data.setdefault("color", "#ffffff")
        self.data.setdefault("max_width", 350)
        self.data.setdefault("alignment", "center")   # left | center | right
        # Effects
        self.data.setdefault("outline_color", "")
        self.data.setdefault("outline_width", 0)
        self.data.setdefault("shadow_color", "")
        self.data.setdefault("shadow_offset_x", 1)
        self.data.setdefault("shadow_offset_y", 1)
        self.data.setdefault("shadow_blur", 0.0)

    @property
    def font_key(self) -> tuple:
        return (
            self.data.get("font_family", "Microsoft YaHei"),
            self.data.get("font_size", 14),
            self.data.get("font_bold", False),
        )


class SyncedLyricsNode(SceneNode):
    """Synced lyrics display with current-line highlighting and smooth transitions.

    Bilingual support (参考 syncedlyrics / YesPlayMusic / LyricsX):
      - bilingual: enable/disable translation display
      - bilingual_mode: "merged" (main+trans in one line) | "separate" (trans on own line)
      - trans_color: translation text color (defaults to dimmer version of main)
      - trans_font_size: translation font size (0 = scale from main font_size * 0.85)
      - trans_font_family: translation font family ("" = use main font_family)
    """

    def __init__(self, node_id: str = "lyrics", **kwargs):
        super().__init__(id=node_id, node_type="synced_lyrics", **kwargs)
        self.data.setdefault("font_family", "Microsoft YaHei")
        self.data.setdefault("font_size", 14)
        self.data.setdefault("font_bold", False)
        self.data.setdefault("color", "#cccccc")
        self.data.setdefault("max_lines", 1)
        self.data.setdefault("max_width", 350)
        self.data.setdefault("bilingual", True)
        self.data.setdefault("bilingual_mode", "merged")  # "merged" | "separate"
        self.data.setdefault("trans_color", "")            # "" = auto (dimmer main color)
        self.data.setdefault("trans_font_size", 0)         # 0 = auto (main * 0.85)
        self.data.setdefault("trans_font_family", "")      # "" = use main font_family
        self.data.setdefault("time_offset_ms", 0)
        self.data.setdefault("line_spacing", 4)
        self.data.setdefault("alignment", "center")  # left | center | right
        # Effects
        self.data.setdefault("outline_color", "")
        self.data.setdefault("outline_width", 0)

    @property
    def font_key(self) -> tuple:
        return (
            self.data.get("font_family", "Arial"),
            self.data.get("font_size", 14),
            self.data.get("font_bold", False),
        )


class ProgressBarNode(SceneNode):
    """Progress bar with optional time text.

    Mode-specific settings are isolated under "bar" and "arc" sub-dicts
    to prevent cross-mode value corruption when switching modes.
    """

    def __init__(self, node_id: str = "progress", **kwargs):
        super().__init__(id=node_id, node_type="progress_bar", **kwargs)
        self.data.setdefault("mode", "bar")              # off | bar | arc | ring | roundrect
        self.data.setdefault("color", "#44cc88")
        self.data.setdefault("background_color", "#333355")
        self.data.setdefault("width", 4)                  # bar height / arc line width (shared)
        # Bar mode specific
        self.data.setdefault("bar", {
            "length_ratio": 0.7,      # fraction of node width
            "direction": "lr",        # lr | rl
            "start_position": "left", # left | right
        })
        # Arc/Ring/Roundrect mode specific
        self.data.setdefault("arc", {
            "start_position": "top",  # top | right | bottom | left
            "direction": "cw",        # cw | ccw
            "cap_style": "round",     # round | flat
            "supersample": "2X",      # 2X | 4X | 6X
            "position": "edge",       # edge | inside | outside — relative to cover
            "distance": 0,            # pixels offset from cover edge (inside→inward, outside→outward)
        })
        # ★ Legacy flat keys — read by painter as fallback, not written by settings
        self.data.setdefault("length_ratio", 0.7)
        self.data.setdefault("direction", "lr")
        self.data.setdefault("start_position", "left")
        self.data.setdefault("cap_style", "round")
        self.data.setdefault("position_mode", "cover")   # cover | window
        # Time text (nested structure, matches config schema)
        self.data.setdefault("time_text", {
            "visible": True,
            "position": "bottom",
            "offset_x": 0,           # fine-tune X offset (px)
            "offset_y": 0,           # fine-tune Y offset (px)
            "font": {"family": "Microsoft YaHei", "size": 8, "bold": False},
            "color": "#cccccc",
            "outline_color": "",
            "outline_width": 0,
        })

    @property
    def time_font_key(self) -> tuple:
        tt = self.data.get("time_text", {})
        font = tt.get("font", {})
        return (
            font.get("family", "Microsoft YaHei"),
            font.get("size", 8),
            False,
        )


class ProgressTimeNode(SceneNode):
    """Standalone progress time display — shows elapsed / total duration."""

    def __init__(self, node_id: str = "progress_time", **kwargs):
        super().__init__(id=node_id, node_type="progress_time", **kwargs)
        self.data.setdefault("font_family", "Microsoft YaHei")
        self.data.setdefault("font_size", 10)
        self.data.setdefault("font_bold", False)
        self.data.setdefault("color", "#cccccc")
        self.data.setdefault("format", "elapsed_total")  # elapsed_total | elapsed | remaining
        self.data.setdefault("separator", " / ")         # text between elapsed and total
        # Effects
        self.data.setdefault("outline_color", "")
        self.data.setdefault("outline_width", 0)

    @property
    def font_key(self) -> tuple:
        return (
            self.data.get("font_family", "Microsoft YaHei"),
            self.data.get("font_size", 10),
            self.data.get("font_bold", False),
        )


class GroupNode(SceneNode):
    """Container node that groups children together.

    Acts as a layout container. Children positions are relative to this node.
    """

    def __init__(self, node_id: str, **kwargs):
        super().__init__(id=node_id, node_type="group", **kwargs)


# Factory
NODE_CLASSES = {
    "cover": CoverNode,
    "text": TextNode,
    "synced_lyrics": SyncedLyricsNode,
    "progress_bar": ProgressBarNode,
    "progress_time": ProgressTimeNode,
    "group": GroupNode,
}


def create_node(node_id: str, node_type: str, **kwargs) -> SceneNode:
    """Create a scene node from type string."""
    cls = NODE_CLASSES.get(node_type, TextNode)
    return cls(node_id=node_id, **kwargs)


def node_from_config(cfg: dict) -> SceneNode:
    """Create a node from config dictionary (matching the config schema)."""
    node_id = cfg["id"]
    node_type = cfg["type"]
    visible = cfg.get("visible", True)

    node = create_node(node_id, node_type)
    node.visible = visible

    # Anchor
    anchor = cfg.get("anchor", {})
    if anchor:
        node.self_anchor = anchor.get("self", "center")
        node.parent_anchor = anchor.get("parent", "top")

    # Offset
    offset = cfg.get("offset", {})
    if offset:
        node.offset_x = offset.get("x", 0)
        node.offset_y = offset.get("y", 0)

    # Type-specific data
    _copy_data(cfg, node.data)

    return node


def _copy_data(src: dict, dst: dict) -> None:
    """Copy relevant keys from src config to node data dict."""
    # Known keys that go to node attributes, not data
    attr_keys = {"id", "type", "visible", "anchor", "offset", "children"}

    for key, value in src.items():
        if key in attr_keys:
            continue
        # Flatten nested font/effect configs into data dict
        if key == "font" and isinstance(value, dict):
            dst["font_family"] = value.get("family", dst.get("font_family", "Microsoft YaHei"))
            dst["font_size"] = value.get("size", dst.get("font_size", 14))
            dst["font_bold"] = value.get("bold", dst.get("font_bold", False))
        elif key == "effects" and isinstance(value, dict):
            outline = value.get("outline", {})
            if outline:
                dst["outline_color"] = outline.get("color", "")
                dst["outline_width"] = outline.get("width", 0)
            shadow = value.get("shadow", {})
            if shadow:
                dst["shadow_color"] = shadow.get("color", "")
                dst["shadow_offset_x"] = shadow.get("offset_x", 1)
                dst["shadow_offset_y"] = shadow.get("offset_y", 1)
                dst["shadow_blur"] = shadow.get("blur", 0)
        elif key == "spin" and isinstance(value, dict):
            # Pass through nested (compositor reads node.data.get("spin", {}))
            dst["spin"] = deepcopy(value)
        elif key == "time_text" and isinstance(value, dict):
            # Pass through nested as-is (compositor reads nested, settings writes nested)
            dst["time_text"] = deepcopy(value)
        elif key == "size" and node_type_is_cover(src):
            dst["size"] = value
        else:
            dst[key] = value


def node_type_is_cover(cfg: dict) -> bool:
    return cfg.get("type") == "cover"
