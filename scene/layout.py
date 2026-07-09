"""Layout resolver — computes absolute pixel positions for all nodes.

Supports anchor-based positioning with:
- 9 self-anchors (top-left, top, top-right, left, center, right, bottom-left, bottom, bottom-right)
- 9 parent-anchors (absolute positions within parent)
- Relative anchors to sibling nodes (below title, right_of cover, etc.)
"""
from scene.node import SceneNode


def resolve_layout(root: SceneNode) -> None:
    """Resolve absolute positions for all children of root.

    Mutates node._abs_x, _abs_y, _abs_width, _abs_height, _resolved in place.
    """
    # Pass 1: compute sizes for nodes with auto-size
    _compute_sizes(root)

    # Pass 2: resolve positions (top-down, dependency order)
    _resolve_positions(root)


def _compute_sizes(root: SceneNode) -> None:
    """Compute width/height for auto-sized nodes."""
    for node in root.children:
        if node.node_type == "cover":
            node.width = node.data.get("size", 180)
            node.height = node.width
        elif node.node_type == "progress_bar":
            node.width = root.width * 0.9  # default to 90% of parent
            node.height = 30
        elif node.node_type == "progress_time":
            node.width = 200   # enough for "00:00 / 00:00" at typical font sizes
            font_size = node.data.get("font_size", 10)
            node.height = font_size + 8
        elif node.node_type in ("text", "synced_lyrics"):
            max_w = node.data.get("max_width", root.width - 40)
            node.width = min(float(max_w), root.width - 20)

            # Estimate height from font size and expected lines
            font_size = node.data.get("font_size", 14)
            line_height = float(font_size) + 4
            if node.node_type == "synced_lyrics":
                max_lines = int(float(node.data.get("max_lines", 1)))
                bilingual = node.data.get("bilingual", True)
                bilingual_mode = node.data.get("bilingual_mode", "merged")
                if bilingual:
                    visual_lines = max_lines * 2  # main + trans per line pair
                    # Extra spacing: inter-pair gaps + main-trans gap
                    extra = (max_lines - 1) * 6 + 6
                    if bilingual_mode == "separate":
                        extra = max_lines * 6 + (max_lines - 1) * 8
                    node.height = line_height * visual_lines + extra + 8
                else:
                    node.height = line_height * max_lines + 8
            else:
                node.height = line_height * 2 + 4  # estimate 2 lines max

        # Recurse into groups
        if node.node_type == "group":
            _compute_sizes(node)


def _resolve_positions(root: SceneNode) -> None:
    """Resolve positions for all children of a node.
    Handles inter-node dependencies (anchors referencing sibling nodes).
    """
    # First pass: resolve nodes with absolute parent anchors
    for node in root.children:
        if _is_absolute_anchor(node.parent_anchor):
            _place_absolute(node, root.width, root.height)
            node._resolved = True

    # Second pass: resolve nodes with relative anchors (to siblings)
    # Iterate until all resolved, with dependency resolution
    max_iterations = 10
    for _ in range(max_iterations):
        all_resolved = True
        for node in root.children:
            if node._resolved:
                continue
            if _is_relative_anchor(node.parent_anchor):
                ref_node_id = _get_reference_node_id(node.parent_anchor)
                ref_node = _find_sibling(root, ref_node_id)
                if ref_node and ref_node._resolved:
                    _place_relative(node, ref_node)
                    node._resolved = True
                else:
                    all_resolved = False
        if all_resolved:
            break

    # Third pass: any still unresolved → place at default (top-center)
    for node in root.children:
        if not node._resolved:
            _place_absolute(node, root.width, root.height)
            node._resolved = True

    # Recurse into groups
    for node in root.children:
        if node.node_type == "group":
            _resolve_positions(node)


def _is_absolute_anchor(anchor: str) -> bool:
    """Check if anchor is an absolute position within parent."""
    absolute = {
        "top-left", "top", "top-right",
        "left", "center", "right",
        "bottom-left", "bottom", "bottom-right",
    }
    return anchor in absolute


def _is_relative_anchor(anchor: str) -> bool:
    """Check if anchor references a sibling node."""
    return any(anchor.startswith(prefix) for prefix in
               ("below ", "above ", "right_of ", "left_of ", "bottom_of ", "top_of "))


def _get_reference_node_id(anchor: str) -> str:
    """Extract the reference node ID from a relative anchor string.
    e.g., "bottom_of cover" → "cover"
    """
    parts = anchor.split(" ", 1)
    return parts[1] if len(parts) > 1 else ""


def _find_sibling(root: SceneNode, node_id: str) -> SceneNode | None:
    """Find a sibling node by ID."""
    for child in root.children:
        if child.id == node_id:
            return child
    return None


def _place_absolute(node: SceneNode, parent_w: float, parent_h: float) -> None:
    """Place node using absolute parent anchor.

    The parent_anchor specifies where in the parent this node is anchored.
    The self_anchor specifies where on the node the anchor point is.
    offset_x/offset_y are added after positioning.
    """
    # Position of parent anchor point in parent coordinates
    px, py = _anchor_point(parent_w, parent_h, node.parent_anchor)

    # Position of self anchor on node
    sx, sy = _anchor_point(node.width, node.height, node.self_anchor)

    node._abs_x = px - sx + node.offset_x
    node._abs_y = py - sy + node.offset_y
    node._abs_width = node.width
    node._abs_height = node.height


def _place_relative(node: SceneNode, ref_node: SceneNode) -> None:
    """Place node relative to a sibling reference node.

    parent_anchor format: "direction ref_id"
      - "below cover" → below the cover node
      - "right_of cover" → to the right of the cover node
      - "bottom_of cover" → aligned at the bottom of the cover node
    """
    direction = node.parent_anchor.split(" ", 1)[0]

    # Reference node bounds
    rx, ry = ref_node.abs_x, ref_node.abs_y
    rw, rh = ref_node.abs_width, ref_node.abs_height

    # Self anchor offset
    sx, sy = _anchor_point(node.width, node.height, node.self_anchor)

    if direction == "below" or direction == "bottom_of":
        # Align horizontally with ref, place below
        ref_center_x = rx + rw / 2
        node._abs_x = ref_center_x - sx + node.offset_x
        node._abs_y = ry + rh + node.offset_y
    elif direction == "above" or direction == "top_of":
        ref_center_x = rx + rw / 2
        node._abs_x = ref_center_x - sx + node.offset_x
        node._abs_y = ry - node.height + node.offset_y
    elif direction == "right_of":
        ref_center_y = ry + rh / 2
        node._abs_x = rx + rw + node.offset_x
        node._abs_y = ref_center_y - sy + node.offset_y
    elif direction == "left_of":
        ref_center_y = ry + rh / 2
        node._abs_x = rx - node.width + node.offset_x
        node._abs_y = ref_center_y - sy + node.offset_y

    node._abs_width = node.width
    node._abs_height = node.height


def _anchor_point(width: float, height: float, anchor: str) -> tuple[float, float]:
    """Convert an anchor string to a (x, y) point within a rectangle of (width, height)."""
    x_map = {
        "top-left": 0, "left": 0, "bottom-left": 0,
        "top": width / 2, "center": width / 2, "bottom": width / 2,
        "top-right": width, "right": width, "bottom-right": width,
    }
    y_map = {
        "top-left": 0, "top": 0, "top-right": 0,
        "left": height / 2, "center": height / 2, "right": height / 2,
        "bottom-left": height, "bottom": height, "bottom-right": height,
    }
    # Default to center if anchor not recognized
    return x_map.get(anchor, width / 2), y_map.get(anchor, height / 2)
