"""A tab's split tree, read from `pane layout`, and the moves that rebuild it beside a viewer.

herdr only splits single panes, so a viewer placed "under the whole tab" is built in three steps:
park every pane but the first (the anchor) in a temporary tab, split the anchor for the viewer,
then move the parked panes back in tree order. Each move splits the pane that already holds the
place of the subtree it belongs to, with that split's own ratio, so the rebuilt tree has the
original shape and ratios above (or beside) the viewer. Closing the viewer removes its split and
leaves the original layout. herdr refuses moves within one tab ("same_tab"), hence the parking.
"""

from __future__ import annotations

from dataclasses import dataclass

DIRECTIONS = frozenset({"right", "down"})
_MAX_DEPTH = 64

Rect = tuple[int, int, int, int]  # x, y, width, height


class LayoutError(ValueError):
    """The layout can't be read as a split tree; callers fall back to a plain split."""


@dataclass(frozen=True)
class Leaf:
    pane_id: str


@dataclass(frozen=True)
class Split:
    direction: str  # "right": first | second; "down": first over second
    ratio: float    # the first child's share
    first: Node
    second: Node


Node = Leaf | Split


@dataclass(frozen=True)
class Step:
    """Move `pane_id` beside `target` (`direction`), leaving `target` the share `ratio`."""
    pane_id: str
    direction: str
    target: str
    ratio: float


def _rect(raw: object, what: str) -> Rect:
    if not isinstance(raw, dict):
        raise LayoutError(f"{what}: rect is not an object")
    values = tuple(raw.get(key) for key in ("x", "y", "width", "height"))
    if not all(isinstance(v, int) and not isinstance(v, bool) and v >= 0 for v in values):
        raise LayoutError(f"{what}: rect needs non-negative integers x, y, width, height")
    if values[2] == 0 or values[3] == 0:
        raise LayoutError(f"{what}: empty rect")
    return values  # type: ignore[return-value]


def _inside(inner: Rect, outer: Rect) -> bool:
    x, y, w, h = inner
    ox, oy, ow, oh = outer
    return ox <= x and oy <= y and x + w <= ox + ow and y + h <= oy + oh


def parse_tree(layout: dict) -> Node:
    """The split tree of one tab. Raises LayoutError on anything it can't account for exactly."""
    area = _rect(layout.get("area"), "area")
    panes: dict[str, Rect] = {}
    for entry in layout.get("panes") or []:
        pane_id = entry.get("pane_id") if isinstance(entry, dict) else None
        if not isinstance(pane_id, str) or not pane_id or pane_id in panes:
            raise LayoutError("pane entry without a unique pane_id")
        panes[pane_id] = _rect(entry.get("rect"), pane_id)
    splits: dict[Rect, tuple[str, float]] = {}
    for entry in layout.get("splits") or []:
        if not isinstance(entry, dict):
            raise LayoutError("split entry is not an object")
        direction, ratio = entry.get("direction"), entry.get("ratio")
        if direction not in DIRECTIONS:
            raise LayoutError(f"unknown split direction {direction!r}")
        if isinstance(ratio, bool) or not isinstance(ratio, (int, float)) or not 0 < ratio < 1:
            raise LayoutError(f"bad split ratio {ratio!r}")
        rect = _rect(entry.get("rect"), "split")
        if rect in splits:
            raise LayoutError("two splits share one rect")
        splits[rect] = (direction, float(ratio))
    if not panes:
        raise LayoutError("no panes")

    def build(rect: Rect, depth: int) -> Node:
        if depth > _MAX_DEPTH:
            raise LayoutError("split tree too deep")
        inside = [(p, r) for p, r in panes.items() if _inside(r, rect)]
        if len(inside) == 1 and inside[0][1] == rect:
            return Leaf(inside[0][0])
        if rect not in splits:
            raise LayoutError(f"no split covers {rect}")
        direction, ratio = splits[rect]
        x, y, w, h = rect
        lo, size = (x, w) if direction == "right" else (y, h)
        starts = {(r[0] if direction == "right" else r[1]) for _, r in inside}
        spans = [((r[0], r[2]) if direction == "right" else (r[1], r[3])) for _, r in inside]
        cuts = [c for c in starts if lo < c < lo + size
                and not any(s < c < s + n for s, n in spans)]
        if not cuts:
            raise LayoutError(f"no clean cut in {rect}")
        cut = min(cuts, key=lambda c: (abs(c - (lo + ratio * size)), c))
        if direction == "right":
            first, second = (x, y, cut - x, h), (cut, y, x + w - cut, h)
        else:
            first, second = (x, y, w, cut - y), (x, cut, w, y + h - cut)
        return Split(direction, ratio, build(first, depth + 1), build(second, depth + 1))

    tree = build(area, 0)
    if sorted(leaves(tree)) != sorted(panes):
        raise LayoutError("the tree does not account for every pane")
    return tree


def leaves(node: Node) -> list[str]:
    if isinstance(node, Leaf):
        return [node.pane_id]
    return leaves(node.first) + leaves(node.second)


def first_leaf(node: Node) -> str:
    while isinstance(node, Split):
        node = node.first
    return node.pane_id


def rebuild_steps(node: Node) -> list[Step]:
    """Moves that rebuild `node` inside the place of its first leaf (the anchor), in order.

    Every step's target is the anchor or a pane an earlier step already placed."""
    steps: list[Step] = []

    def walk(n: Node) -> None:
        if isinstance(n, Split):
            steps.append(Step(first_leaf(n.second), n.direction, first_leaf(n.first), n.ratio))
            walk(n.first)
            walk(n.second)

    walk(node)
    return steps
