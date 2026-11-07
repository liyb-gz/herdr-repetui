"""The bar spans the whole tab: other panes are parked in a temporary tab and moved back around it."""

from dataclasses import replace

import pytest
from test_actions import FakeHerdr, env  # noqa: F401  (fixture)

from herdr_repetui.actions import toggle
from herdr_repetui.herdr import HerdrError
from herdr_repetui.layout import Leaf, Split, leaves
from herdr_repetui.state import load_state


class TreeHerdr(FakeHerdr):
    """FakeHerdr whose tab w1:t1 holds a real split tree, changed by moves the way herdr does."""

    def __init__(self, sock, tree, *, fail=()):
        super().__init__(sock)
        self.tree = tree
        self.base = {(self.socket_path, p): "w1:t1" for p in leaves(tree)}
        self.parked: dict[str, str] = {}
        self.fail = set(fail)                  # ("park", pane) / ("back", pane) / ("viewer", None)

    # layout of w1:t1 as herdr reports it: rects + splits
    def layout(self, pane_id):
        panes, splits = [], []

        def walk(node, x, y, w, h):
            if isinstance(node, Leaf):
                panes.append({"pane_id": node.pane_id, "rect": {"x": x, "y": y, "width": w, "height": h}})
                return
            splits.append({"direction": node.direction, "ratio": node.ratio,
                           "rect": {"x": x, "y": y, "width": w, "height": h}})
            if node.direction == "right":
                a = round(w * node.ratio)
                walk(node.first, x, y, a, h)
                walk(node.second, x + a, y, w - a, h)
            else:
                a = round(h * node.ratio)
                walk(node.first, x, y, w, a)
                walk(node.second, x, y + a, w, h - a)

        walk(self.tree, 0, 0, 240, 72)
        return {"area": {"x": 0, "y": 0, "width": 240, "height": 72}, "panes": panes, "splits": splits,
                "zoomed": False}

    def _remove(self, node, pane):
        if isinstance(node, Leaf):
            return None if node.pane_id == pane else node
        a, b = self._remove(node.first, pane), self._remove(node.second, pane)
        return b if a is None else a if b is None else Split(node.direction, node.ratio, a, b)

    def _insert(self, node, target, direction, ratio, new):
        if isinstance(node, Leaf):
            return Split(direction, ratio, node, new) if node.pane_id == target else node
        return Split(node.direction, node.ratio, self._insert(node.first, target, direction, ratio, new),
                     self._insert(node.second, target, direction, ratio, new))

    def park(self, pane_id, label):
        self.calls.append(("park", pane_id, label))
        if ("park", pane_id) in self.fail:
            raise HerdrError("server_error", "park failed")
        self.tree = self._remove(self.tree, pane_id)
        self.parked[pane_id] = "w1:t9"
        self.base[(self.socket_path, pane_id)] = "w1:t9"
        return pane_id, "w1:t9"

    def move_beside(self, pane_id, tab_id, target, direction, ratio, *, focus=True):
        if (self.socket_path, pane_id) in self.viewers:          # the viewer itself
            if ("viewer", None) in self.fail:
                raise HerdrError("server_error", "viewer move failed")
            moved = super().move_beside(pane_id, tab_id, target, direction, ratio)
            self.tree = self._insert(self.tree, target, direction, ratio, Leaf(moved))
            return moved
        self.calls.append(("pane", pane_id, tab_id, target, direction, focus))
        if tab_id == "w1:t9":                                    # parking beside the first parked pane
            if ("park", pane_id) in self.fail:
                raise HerdrError("server_error", "park failed")
            self.tree = self._remove(self.tree, pane_id)
            self.parked[pane_id] = "w1:t9"
            self.base[(self.socket_path, pane_id)] = "w1:t9"
            return pane_id
        if ("back", pane_id) in self.fail:
            raise HerdrError("server_error", "move back failed")
        assert pane_id in self.parked, "herdr refuses moves within one tab (same_tab)"
        if target not in leaves(self.tree):
            raise HerdrError("pane_not_found", f"{target} is not in {tab_id}")
        del self.parked[pane_id]
        self.base[(self.socket_path, pane_id)] = "w1:t1"
        self.tree = self._insert(self.tree, target, direction, ratio, Leaf(pane_id))
        return pane_id

    def close_viewer(self, pane_id, socket_path):
        super().close_viewer(pane_id, socket_path)
        self.tree = self._remove(self.tree, pane_id) or self.tree


SIDE_BY_SIDE = Split("right", 0.5, Leaf("w1:p1"), Leaf("w1:p2"))
THREE = Split("right", 0.6, Leaf("w1:p1"), Split("down", 0.5, Leaf("w1:p2"), Leaf("w1:p3")))


@pytest.fixture
def tree_env(env):  # noqa: F811
    config, herdr, ctx = env

    def make(tree, **kw):
        fake = TreeHerdr(herdr.sock, tree, **kw)
        fake.mac = herdr.mac
        return fake

    return config, make, ctx


@pytest.mark.parametrize("placement,direction", [("bottom", "down"), ("side", "right")])
@pytest.mark.parametrize("tree", [SIDE_BY_SIDE, THREE])
def test_the_bar_spans_the_whole_tab_and_hiding_restores_the_layout(tree_env, tree, placement, direction):
    config, make, ctx = tree_env
    herdr = make(tree)
    assert toggle(placement, ctx, herdr, config) == "shown"
    viewer, _ = load_state(config.state_path)
    assert isinstance(herdr.tree, Split) and herdr.tree.direction == direction
    assert herdr.tree.first == tree and herdr.tree.second == Leaf(viewer.pane_id)   # under/beside everything
    assert herdr.tree.ratio == pytest.approx(0.75 if placement == "bottom" else 0.8)
    assert not herdr.parked                                     # nothing left in the temporary tab
    assert viewer.origin_pane_id == ctx.focused_pane_id         # hiding still returns focus there
    assert herdr.calls[-1] == ("focus", viewer.pane_id)
    assert all(c[5] is False for c in herdr.calls if c[0] == "pane")   # only the viewer takes focus
    assert toggle(placement, ctx, herdr, config) == "hidden"
    assert herdr.tree == tree


def test_parked_panes_go_to_one_labelled_tab(tree_env):
    config, make, ctx = tree_env
    herdr = make(THREE)
    toggle("bottom", ctx, herdr, config)
    assert ("park", "w1:p2", "repetui-layout") in herdr.calls
    assert ("pane", "w1:p3", "w1:t9", "w1:p2", "right", False) in herdr.calls


def test_span_tab_off_is_the_old_split_under_the_focused_pane(tree_env):
    config, make, ctx = tree_env
    herdr = make(SIDE_BY_SIDE)
    toggle("bottom", ctx, herdr, replace(config, span_tab=False))
    viewer, _ = load_state(config.state_path)
    assert not any(c[0] == "park" for c in herdr.calls)
    assert herdr.tree == Split("right", 0.5, Leaf("w1:p1"), Split("down", pytest.approx(0.75),
                                                                     Leaf("w1:p2"), Leaf(viewer.pane_id)))


def test_a_single_pane_tab_needs_no_parking(tree_env):
    config, make, ctx = tree_env
    herdr = make(Leaf("w1:p2"))
    assert toggle("bottom", ctx, herdr, config) == "shown"
    assert not any(c[0] in {"park", "pane"} for c in herdr.calls)


def test_an_unreadable_layout_falls_back_to_the_plain_split(tree_env, monkeypatch):
    config, make, ctx = tree_env
    herdr = make(SIDE_BY_SIDE)
    real = herdr.layout
    monkeypatch.setattr(herdr, "layout", lambda p: {**real(p), "splits": []})
    assert toggle("bottom", ctx, herdr, config) == "shown"
    assert not any(c[0] == "park" for c in herdr.calls)


def test_a_failed_viewer_move_puts_every_pane_back(tree_env):
    config, make, ctx = tree_env
    herdr = make(THREE, fail={("viewer", None)})
    with pytest.raises(HerdrError):
        toggle("bottom", ctx, herdr, config)
    assert herdr.tree == THREE and not herdr.parked


def test_a_failed_park_puts_back_what_moved_and_reports(tree_env):
    config, make, ctx = tree_env
    herdr = make(THREE, fail={("park", "w1:p3")})
    with pytest.raises(HerdrError):
        toggle("bottom", ctx, herdr, config)
    assert sorted(leaves(herdr.tree)) == ["w1:p1", "w1:p2", "w1:p3"] and not herdr.parked
    assert not herdr.viewers                                    # the opened viewer was closed again


def test_a_pane_that_will_not_come_back_is_named_and_the_rest_still_return(tree_env):
    config, make, ctx = tree_env
    herdr = make(THREE, fail={("back", "w1:p2")})
    with pytest.raises(HerdrError) as info:
        toggle("bottom", ctx, herdr, config)
    # p3's place is beside p2, so it is stuck too; both are named, and both are still running
    assert "w1:p2, w1:p3" in str(info.value) and "repetui-layout" in str(info.value)
    viewer, _ = load_state(config.state_path)
    assert viewer is not None                                   # the viewer is ours and recorded


def test_a_failed_move_back_does_not_stop_later_independent_panes(tree_env):
    config, make, ctx = tree_env
    four = Split("right", 0.5, Split("down", 0.5, Leaf("w1:p1"), Leaf("w1:p2")),
                 Split("down", 0.5, Leaf("w1:p3"), Leaf("w1:p4")))
    herdr = make(four, fail={("back", "w1:p2")})
    with pytest.raises(HerdrError) as info:
        toggle("bottom", ctx, herdr, config)
    assert "w1:p2" in str(info.value) and "w1:p3" not in str(info.value)
    assert set(herdr.parked) == {"w1:p2"}                       # p3 and p4 came back regardless
