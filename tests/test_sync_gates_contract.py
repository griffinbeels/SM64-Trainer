"""The gate contract: registration is loud, ordering follows needs, verdicts
round-trip. Registry state is module-global, so each test starts it empty."""
import pytest

from sm64_events.sync import gates as G


def _ok(ctx):
    return G.Verdict("verified")


@pytest.fixture(autouse=True)
def _empty_registry():
    saved = list(G.GATES)
    G.GATES.clear()
    yield
    G.GATES.clear()
    G.GATES.extend(saved)


def test_register_and_order_follow_needs():
    G.register(G.Gate("b", "version", "address", "i", "p", _ok, needs=("a",)),
               G.Gate("a", "version", "address", "i", "p", _ok))
    assert [g.id for g in G.ordered()] == ["a", "b"]


def test_unknown_need_and_cycle_fail_loudly():
    G.register(G.Gate("x", "version", "address", "i", "p", _ok, needs=("nope",)))
    with pytest.raises(ValueError, match="unknown"):
        G.ordered()
    G.GATES.clear()
    G.register(G.Gate("x", "version", "address", "i", "p", _ok, needs=("y",)),
               G.Gate("y", "version", "address", "i", "p", _ok, needs=("x",)))
    with pytest.raises(ValueError, match="cycle"):
        G.ordered()


def test_gate_rejects_bad_feature_kind_blank_text_and_duplicate_id():
    with pytest.raises(ValueError):
        G.register(G.Gate("x", "not a feature", "address", "i", "p", _ok))
    with pytest.raises(ValueError):
        G.register(G.Gate("x", "version", "nope", "i", "p", _ok))
    with pytest.raises(ValueError):
        G.register(G.Gate("x", "version", "address", " ", "p", _ok))
    G.register(G.Gate("x", "version", "address", "i", "p", _ok))
    with pytest.raises(ValueError, match="duplicate"):
        G.register(G.Gate("x", "version", "address", "i", "p", _ok))
    assert G.gate("x").id == "x"
    assert G.by_feature()["version"][0].id == "x"
    assert G.as_json()[0]["id"] == "x"


def test_verdict_roundtrips_json_and_rejects_unknown_status():
    verdict = G.Verdict("failed", value=0x8032C694, measured={"delta": 3},
                        evidence="e", frames=12)
    assert G.Verdict.from_json(verdict.as_json()) == verdict
    with pytest.raises(ValueError):
        G.Verdict("meh")


def test_reads_label_names_what_each_kind_reads():
    """The dashboard's READS column: one derivation, server-side."""
    assert G.reads_label(G.Gate("address.global_timer", "version", "address", "i", "p", _ok)) == "gGlobalTimer"
    assert G.reads_label(G.Gate("address.usamune_overall", "version", "address", "i", "p", _ok)) == "hunt: usamune_time"
    assert G.reads_label(G.Gate("address.object_pool.confirm", "version", "address", "i", "p", _ok)) == "gObjectPool"
    assert G.reads_label(G.Gate("version.rom", "version", "address", "i", "p", _ok)) == "the ROM header"
    assert G.reads_label(G.Gate("cal.x", "star grab", "calibration", "i", "p", _ok,
                                backs="sm64_events.detectors.igt_clock.IgtClock.DISPLAY_TICK")) == "IgtClock.DISPLAY_TICK"
    assert G.reads_label(G.Gate("feature.star_collected.ground", "star grab", "feature", "i", "p", _ok)) == "star_collected"
    assert G.reads_label(G.Gate("behaviour.base", "version", "behaviour", "i", "p", _ok)) == "bhvMario → base"
    gate = G.Gate("address.curr_level", "castle areas", "address", "i", "p", _ok)
    assert gate.as_json()["reads"] == "gCurrLevelNum"
