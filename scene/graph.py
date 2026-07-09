"""Scene graph — manages the tree of visual nodes.

The scene graph is the single source of truth for:
- What to render (nodes with types and data)
- Where to render (positions resolved by layout engine)
- Visual state (animatable properties)
"""
from typing import Optional, Iterator
from scene.node import SceneNode
from scene.nodes import node_from_config, CoverNode


class SceneGraph:
    """Root container for the visual scene.

    Usage:
        graph = SceneGraph()
        graph.load_from_config(config["scene"])
        graph.resolve_layout(window_width, window_height)
        # Render each visible node:
        for node in graph.render_order():
            render_node(node)
    """

    def __init__(self):
        self.root = SceneNode(id="root", node_type="group")
        self.root.width = 400
        self.root.height = 550
        self._node_map: dict[str, SceneNode] = {}
        self._layout_dirty = True

    # ---- Building ----

    def load_from_config(self, scene_config: dict) -> None:
        """Build scene graph from declarative config dict."""
        self.clear()
        nodes_cfg = scene_config.get("nodes", [])
        for node_cfg in nodes_cfg:
            node = node_from_config(node_cfg)
            self.root.add_child(node)
            self._node_map[node.id] = node

    def clear(self) -> None:
        """Remove all nodes."""
        self.root.children.clear()
        self._node_map.clear()
        self._layout_dirty = True

    def add_node(self, node: SceneNode) -> None:
        """Add a node to the scene."""
        self.root.add_child(node)
        self._node_map[node.id] = node
        self._layout_dirty = True

    def remove_node(self, node_id: str) -> None:
        """Remove a node by ID."""
        node = self._node_map.pop(node_id, None)
        if node:
            self.root.remove_child(node)
            self._layout_dirty = True

    def get(self, node_id: str) -> Optional[SceneNode]:
        """Get a node by ID."""
        return self._node_map.get(node_id)

    def find(self, node_id: str) -> Optional[SceneNode]:
        """Find a node by ID (searches recursively)."""
        return self.root.find(node_id)

    # ---- Layout ----

    def resolve_layout(self, width: float, height: float) -> None:
        """Resolve all node positions within the given container size."""
        from scene.layout import resolve_layout
        self.root.width = width
        self.root.height = height
        resolve_layout(self.root)
        self._layout_dirty = False

    @property
    def layout_dirty(self) -> bool:
        return self._layout_dirty

    def mark_dirty(self) -> None:
        self._layout_dirty = True

    # ---- Iteration ----

    def render_order(self) -> Iterator[SceneNode]:
        """Yield nodes in render order (by z_index, then tree order).
        Only returns leaf nodes (type != "group"), skipping invisible ones.
        """
        flat = []
        self._flatten_render(self.root, flat)
        flat.sort(key=lambda n: n.z_index)  # stable sort preserves insertion order
        for node in flat:
            if node.visible and node.node_type != "group":
                yield node

    def _flatten_render(self, node: SceneNode, result: list[SceneNode]) -> None:
        for child in node.children:
            if child.node_type == "group":
                self._flatten_render(child, result)
            else:
                result.append(child)

    def all_nodes(self) -> Iterator[SceneNode]:
        """Yield all nodes (including groups) in depth-first order."""
        yield from self._walk(self.root)

    def _walk(self, node: SceneNode) -> Iterator[SceneNode]:
        for child in node.children:
            yield child
            yield from self._walk(child)

    # ---- Snapshot ----

    def snapshot(self) -> dict[str, dict]:
        """Capture current visual state of all nodes.
        Used for freeze-frame during transitions.
        Returns {node_id: {opacity, rotation, scale_x, scale_y, ...}}
        """
        snap = {}
        for node in self.all_nodes():
            snap[node.id] = {
                "opacity": node.opacity,
                "rotation": node.rotation,
                "scale_x": node.scale_x,
                "scale_y": node.scale_y,
                "offset_x": node.offset_x,
                "offset_y": node.offset_y,
                "visible": node.visible,
            }
        return snap

    def restore(self, snapshot: dict) -> None:
        """Restore visual state from a snapshot."""
        for node_id, state in snapshot.items():
            node = self._node_map.get(node_id)
            if node:
                for key, value in state.items():
                    setattr(node, key, value)

    # ---- Convenience ----

    def get_cover_node(self) -> Optional[CoverNode]:
        """Get the cover node (first found)."""
        for node in self.all_nodes():
            if isinstance(node, CoverNode):
                return node
        return None

    def get_visible_bounds(self) -> tuple[float, float, float, float]:
        """Get bounding box of all visible nodes. Returns (x, y, w, h)."""
        min_x, min_y = float('inf'), float('inf')
        max_x, max_y = float('-inf'), float('-inf')
        for node in self.render_order():
            if not node.visible:
                continue
            x, y = node.abs_x, node.abs_y
            w, h = node.abs_width, node.abs_height
            min_x = min(min_x, x)
            min_y = min(min_y, y)
            max_x = max(max_x, x + w)
            max_y = max(max_y, y + h)
        if min_x == float('inf'):
            return (0, 0, 0, 0)
        return (min_x, min_y, max_x - min_x, max_y - min_y)
