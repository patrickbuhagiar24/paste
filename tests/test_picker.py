"""Picker logic tests (no Tk): filtering, navigation and labels."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pastex.picker import MAX_ROWS, PickerModel, label_for, popup_geometry
from pastex.snippets import Node


def _node(root: Path, relative: str) -> Node:
    path = root / relative
    return Node(name=path.stem, path=path, is_dir=False, source_root=root)


def test_label_shows_the_folder_not_the_absolute_path(tmp_path):
    node = _node(tmp_path, "Email/Follow Up.txt")

    assert label_for(node) == "Email/Follow Up"


def test_model_filters_and_resets_the_selection(tmp_path):
    general = _node(tmp_path, "General/Greeting.txt")
    email = _node(tmp_path, "Email/Reply.txt")
    model = PickerModel(lambda query: [general, email])
    model.move(1)
    assert model.index == 1

    matches = model.set_query("greet")

    assert matches == [general, email]  # provider decides; the model accepts it
    assert model.index == 0
    assert model.query == "greet"


def test_move_wraps_and_keeps_one_item_stable(tmp_path):
    nodes = [_node(tmp_path, "A.txt"), _node(tmp_path, "B.txt")]
    model = PickerModel(lambda query: nodes)

    assert model.current() is nodes[0]
    model.move(-1)
    assert model.current() is nodes[1]
    model.move(1)
    assert model.current() is nodes[0]


def test_empty_matches_are_handled(tmp_path):
    model = PickerModel(lambda query: [])

    assert model.current() is None
    assert model.move(1) is None
    assert model.set_index(3) is None
    assert model.labels() == []


def test_labels_are_capped_for_the_listbox(tmp_path):
    nodes = [_node(tmp_path, f"Snippet {index}.txt") for index in range(30)]
    model = PickerModel(lambda query: nodes)

    assert len(model.labels()) == MAX_ROWS
    assert model.labels()[0] == "Snippet 0"


def test_popup_geometry_stays_on_screen():
    assert popup_geometry(500, 400) == "460x340+460+376"
    assert popup_geometry(5, 5) == "460x340+0+0"
    assert popup_geometry(500, 400, 300, 200) == "300x200+460+376"
