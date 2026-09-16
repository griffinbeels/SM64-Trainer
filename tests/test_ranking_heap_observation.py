"""A concurrent resource snapshot must not break immutable ranking results."""
import gc

from sm64_events.library import populations
from sm64_events.ranks import curves
from sm64_events.ranks.policy import RankingPolicy


def test_curve_preparation_survives_a_retained_heap_snapshot(monkeypatch):
    nodes = [[1000, 95], [1500, 70], [2200, 35], [3000, 5]]
    expected = curves.compile_curve(nodes)
    curves._prepare.cache_clear()
    original = curves.frame_position
    held = []

    def observed(time):
        if time == 1500:
            held.append(gc.get_objects())
        return original(time)

    monkeypatch.setattr(curves, "frame_position", observed)
    try:
        assert curves.compile_curve(nodes) == expected
    finally:
        held.clear()
        curves._prepare.cache_clear()


def test_family_collection_survives_a_retained_heap_snapshot(monkeypatch):
    rows = [{"row_id": name, "name": name, "entries": [
        {"runner": "Player", "time_cs": time}]} for name, time in (("Slow", 2000), ("Fast", 1000))]
    settings = RankingPolicy().resolve("") | {"families": [
        {"id": name, "label": name, "rows": [name]} for name in ("Slow", "Fast")]}
    expected = populations.collect_populations(rows, settings=settings)
    original = populations.FamilyPopulation
    held = []

    def observed(*args, **kwargs):
        held.append(gc.get_objects())
        return original(*args, **kwargs)

    monkeypatch.setattr(populations, "FamilyPopulation", observed)
    try:
        assert populations.collect_populations(rows, settings=settings) == expected
    finally:
        held.clear()
