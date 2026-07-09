"""Scene node — the fundamental visual element in the scene graph.

Each node has:
- Identity (id, type)
- Anchor-based positioning (relative to parent or other nodes)
- Animatable visual properties (opacity, rotation, scale)
- Type-specific data payload
- Parent/child hierarchy
"""
from dataclasses import dataclass, field
from typing import Any, Optional


# Valid self-anchor points (where on THIS element the anchor point is)
SELF_ANCHORS = {
    "top-left", "top", "top-right",
    "left", "center", "right",
    "bottom-left", "bottom", "bottom-right",
}

# Valid parent-anchor references
PARENT_ANCHORS = SELF_ANCHORS | {
    # Relative to another node
    "below cover", "above cover",
    "right_of cover", "left_of cover",
    "bottom_of cover", "top_of cover",
    # ... dynamically generated for any node id
}


@dataclass
class SceneNode:
    """A visual element in the scene graph."""

    id: str
    node_type: str  # "cover", "text", "synced_lyrics", "progress_bar", "group"

    # ---- Positioning ----
    # self_anchor: where on this element the anchor point is (e.g., "center" = middle of element)
    # parent_anchor: where in the parent the anchor point references (e.g., "top" = top-center of parent)
    self_anchor: str = "center"
    parent_anchor: str = "top"
    offset_x: float = 0.0
    offset_y: float = 0.0

    # ---- Size ----
    width: float = 0.0
    height: float = 0.0

    # ---- Visual (all animatable) ----
    opacity: float = 1.0
    rotation: float = 0.0
    scale_x: float = 1.0
    scale_y: float = 1.0
    visible: bool = True

    # ---- Type-specific data ----
    data: dict[str, Any] = field(default_factory=dict)

    # ---- Hierarchy ----
    parent: Optional['SceneNode'] = None
    children: list['SceneNode'] = field(default_factory=list)
    z_index: int = 0

    # ---- Computed (set by layout resolver) ----
    _abs_x: float = 0.0
    _abs_y: float = 0.0
    _abs_width: float = 0.0
    _abs_height: float = 0.0
    _resolved: bool = False

    @property
    def abs_x(self) -> float:
        return self._abs_x

    @property
    def abs_y(self) -> float:
        return self._abs_y

    @property
    def abs_width(self) -> float:
        return self._abs_width

    @property
    def abs_height(self) -> float:
        return self._abs_height

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        """Returns (x, y, width, height) of this node in parent coordinates."""
        ox, oy = self._anchor_offset()
        return (
            self.offset_x - ox,
            self.offset_y - oy,
            self.width,
            self.height,
        )

    def _anchor_offset(self) -> tuple[float, float]:
        """Convert self_anchor string to (x_offset, y_offset) within this element.
        Returns the position of the anchor point relative to the element's top-left corner.
        """
        sw, sh = self.width * self.scale_x, self.height * self.scale_y

        x_map = {
            "top-left": 0, "left": 0, "bottom-left": 0,
            "top": sw / 2, "center": sw / 2, "bottom": sw / 2,
            "top-right": sw, "right": sw, "bottom-right": sw,
        }
        y_map = {
            "top-left": 0, "top": 0, "top-right": 0,
            "left": sh / 2, "center": sh / 2, "right": sh / 2,
            "bottom-left": sh, "bottom": sh, "bottom-right": sh,
        }
        return x_map.get(self.self_anchor, sw / 2), y_map.get(self.self_anchor, sh / 2)

    def add_child(self, node: 'SceneNode') -> None:
        """Add a child node."""
        node.parent = self
        self.children.append(node)

    def remove_child(self, node: 'SceneNode') -> None:
        """Remove a child node."""
        if node in self.children:
            self.children.remove(node)
            node.parent = None

    def find(self, node_id: str) -> Optional['SceneNode']:
        """Find a node by ID in this subtree."""
        if self.id == node_id:
            return self
        for child in self.children:
            found = child.find(node_id)
            if found:
                return found
        return None

    def clone(self) -> 'SceneNode':
        """Deep-clone this node (excluding parent/children)."""
        import copy
        new = SceneNode(
            id=self.id,
            node_type=self.node_type,
            self_anchor=self.self_anchor,
            parent_anchor=self.parent_anchor,
            offset_x=self.offset_x,
            offset_y=self.offset_y,
            width=self.width,
            height=self.height,
            opacity=self.opacity,
            rotation=self.rotation,
            scale_x=self.scale_x,
            scale_y=self.scale_y,
            visible=self.visible,
            data=copy.deepcopy(self.data),
            z_index=self.z_index,
        )
        return new

    def to_dict(self) -> dict:
        """Serialize to dictionary (matching config schema)."""
        return {
            "id": self.id,
            "type": self.node_type,
            "visible": self.visible,
            "anchor": {"self": self.self_anchor, "parent": self.parent_anchor},
            "offset": {"x": self.offset_x, "y": self.offset_y},
            **self.data,
        }
