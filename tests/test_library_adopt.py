from sm64_events.library import adopt


def _ladder(mario, step=1.03):
    ranks = ["Mario", "Grandmaster", "Master", "Diamond",
             "Platinum", "Gold", "Silver", "Bronze"]
    return {rank: round(mario * step ** i, 2) for i, rank in enumerate(ranks)}


def _approach(name, mario, best_cs=None, ladder=True):
    return {"name": name, "best_cs": best_cs if best_cs is not None else int(mario * 100),
            "ladder": _ladder(mario) if ladder else None,
            "entries": [{"time_cs": int(mario * 100), "version": None}] * 40}


def test_the_same_strategy_under_two_names_is_recognised():
    vetted = {"Skyjump": _ladder(30.90)}
    approaches = [_approach("Mario Wings to the Sky", 30.95)]
    assert adopt.match_vetted(vetted, approaches) == {0: "Skyjump"}


def test_a_genuinely_different_strategy_is_not_matched():
    vetted = {"Skyjump": _ladder(30.90)}
    approaches = [_approach("Slope slide strat", 24.00)]
    assert adopt.match_vetted(vetted, approaches) == {}


def test_matching_compares_whole_ladders_not_one_cutoff():
    # JRB's stone pillar is 10.80 JP against 14.50 US while its vetted ladder
    # is US-effective, so a Mario-cutoff-versus-sheet-best test puts the true
    # pair 3.8s apart. The whole-ladder comparison does not care what `best_cs`
    # says.
    vetted = {"Cannon (Direct)": _ladder(14.63)}
    approaches = [_approach("Blast to the Stone Pillar", 14.56, best_cs=1080)]
    assert adopt.match_vetted(vetted, approaches) == {0: "Cannon (Direct)"}


def test_the_closest_pair_claims_its_partner_first():
    # Four strategies within a second of each other: assignment must be greedy
    # over the GLOBALLY closest pair, not in ladder order, or a looser pair
    # steals a partner on the way past.
    vetted = {"far": _ladder(31.50), "near": _ladder(31.00)}
    approaches = [_approach("exact", 31.00)]
    assert adopt.match_vetted(vetted, approaches) == {0: "near"}


def test_one_vetted_strategy_never_claims_two_approaches():
    vetted = {"only": _ladder(31.00)}
    approaches = [_approach("a", 31.00), _approach("b", 31.02)]
    matched = adopt.match_vetted(vetted, approaches)
    assert len(matched) == 1


def test_a_row_with_no_fitted_ladder_is_never_adopted():
    # A strategy in the picker with no ladder grades against nothing, which is
    # worse than not offering it.
    payload = {"targets": [{"entity_key": "star:1:0", "approaches": [
        _approach("Thin one", 30.0, ladder=False)], "subsections": []}]}
    assert adopt.adoptable(payload, {}) == {}


def test_subsections_and_unmapped_targets_are_never_adopted():
    payload = {"targets": [
        {"entity_key": None, "approaches": [_approach("Lobby door", 2.76)],
         "subsections": []},
        {"entity_key": "star:1:0", "approaches": [],
         "subsections": [_approach("Warp fadeout", 15.90)]}]}
    assert adopt.adoptable(payload, {}) == {}


def test_an_unmatched_approach_is_adopted_with_its_ladder():
    payload = {"targets": [{"entity_key": "star:1:4", "approaches": [
        _approach("Skyjump-ish", 30.90), _approach("Slope slide strat", 24.00)],
        "subsections": []}]}
    adopted = adopt.adoptable(payload, {"star:1:4": {"Skyjump": _ladder(30.90)}})
    assert list(adopted) == ["star:1:4"]
    layers = adopted["star:1:4"]
    assert list(layers["strategies"]) == ["Slope slide strat"]
    assert layers["strategies"]["Slope slide strat"]["Mario"] == 24.0
    assert layers["jp_strategies"] == {}       # nothing annotated, so combined


def test_an_approach_whose_name_already_exists_is_not_re_added():
    payload = {"targets": [{"entity_key": "star:1:4", "approaches": [
        _approach("Skyjump", 24.00)], "subsections": []}]}
    assert adopt.adoptable(payload, {"star:1:4": {"Skyjump": _ladder(30.90)}}) == {}
