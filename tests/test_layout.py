import pytest

from herdr_repetui.layout import (
    LayoutError,
    Leaf,
    Split,
    first_leaf,
    leaves,
    parse_tree,
    rebuild_steps,
)


def rect(x, y, w, h):
    return {"x": x, "y": y, "width": w, "height": h}


# Captured from the harness (herdr 0.9.5): p1 | (p2 over p3)
THREE = {
    "area": rect(0, 0, 134, 47),
    "panes": [{"pane_id": "w1:p1", "rect": rect(0, 0, 80, 47)},
              {"pane_id": "w1:p2", "rect": rect(80, 0, 54, 24)},
              {"pane_id": "w1:p3", "rect": rect(80, 24, 54, 23)}],
    "splits": [{"direction": "right", "id": "split_0_root", "ratio": 0.6, "rect": rect(0, 0, 134, 47)},
               {"direction": "down", "id": "split_1_1", "ratio": 0.5, "rect": rect(80, 0, 54, 47)}],
}


def test_reads_the_captured_three_pane_layout() -> None:
    assert parse_tree(THREE) == Split("right", 0.6, Leaf("w1:p1"),
                                      Split("down", 0.5, Leaf("w1:p2"), Leaf("w1:p3")))


def test_a_single_pane_is_a_leaf() -> None:
    layout = {"area": rect(0, 0, 100, 40), "panes": [{"pane_id": "a", "rect": rect(0, 0, 100, 40)}],
              "splits": []}
    assert parse_tree(layout) == Leaf("a")


@pytest.mark.parametrize("nested_first", [True, False])
def test_three_columns_follow_the_split_ratios(nested_first) -> None:
    # (a | b) | c  versus  a | (b | c): the same three rects, told apart by the ratios
    panes = [{"pane_id": "a", "rect": rect(0, 0, 30, 10)}, {"pane_id": "b", "rect": rect(30, 0, 30, 10)},
             {"pane_id": "c", "rect": rect(60, 0, 30, 10)}]
    if nested_first:
        splits = [{"direction": "right", "ratio": 2 / 3, "rect": rect(0, 0, 90, 10)},
                  {"direction": "right", "ratio": 0.5, "rect": rect(0, 0, 60, 10)}]
        want = Split("right", 2 / 3, Split("right", 0.5, Leaf("a"), Leaf("b")), Leaf("c"))
    else:
        splits = [{"direction": "right", "ratio": 1 / 3, "rect": rect(0, 0, 90, 10)},
                  {"direction": "right", "ratio": 0.5, "rect": rect(30, 0, 60, 10)}]
        want = Split("right", 1 / 3, Leaf("a"), Split("right", 0.5, Leaf("b"), Leaf("c")))
    assert parse_tree({"area": rect(0, 0, 90, 10), "panes": panes, "splits": splits}) == want


@pytest.mark.parametrize("broken", [
    {"area": rect(0, 0, 134, 47), "panes": THREE["panes"], "splits": THREE["splits"][:1]},  # split missing
    {**THREE, "panes": THREE["panes"][:2]},                                                 # pane missing
    {**THREE, "splits": [{**THREE["splits"][0], "direction": "left"}, THREE["splits"][1]]},
    {**THREE, "splits": [{**THREE["splits"][0], "ratio": 1.5}, THREE["splits"][1]]},
    {**THREE, "area": {"width": 134, "height": 47}},                                       # no x/y
    {**THREE, "panes": THREE["panes"] + [THREE["panes"][0]]},                               # duplicate id
    {"area": rect(0, 0, 10, 10), "panes": [], "splits": []},
])
def test_anything_unaccounted_for_is_a_layout_error(broken) -> None:
    with pytest.raises(LayoutError):
        parse_tree(broken)


def _replay(anchor: str, steps) -> object:
    """Apply steps the way herdr does: each move splits the target leaf in place."""
    tree = Leaf(anchor)

    def place(node, step):
        if isinstance(node, Leaf):
            if node.pane_id == step.target:
                return Split(step.direction, step.ratio, node, Leaf(step.pane_id))
            return node
        return Split(node.direction, node.ratio, place(node.first, step), place(node.second, step))

    for step in steps:
        assert step.target in leaves(tree), "a step targets a pane that is not placed yet"
        tree = place(tree, step)
    return tree


@pytest.mark.parametrize("tree", [
    Leaf("a"),
    Split("right", 0.6, Leaf("a"), Split("down", 0.5, Leaf("b"), Leaf("c"))),
    Split("down", 0.3, Split("right", 0.5, Leaf("a"), Leaf("b")), Split("right", 0.7, Leaf("c"), Leaf("d"))),
    Split("right", 0.25, Split("down", 0.4, Split("right", 0.5, Leaf("a"), Leaf("e")), Leaf("b")),
          Split("down", 0.6, Leaf("c"), Split("right", 0.2, Leaf("d"), Leaf("f")))),
])
def test_rebuild_steps_recreate_the_tree_from_its_anchor(tree) -> None:
    steps = rebuild_steps(tree)
    assert sorted(s.pane_id for s in steps) == sorted(leaves(tree)[1:])   # every pane but the anchor, once
    assert _replay(first_leaf(tree), steps) == tree
