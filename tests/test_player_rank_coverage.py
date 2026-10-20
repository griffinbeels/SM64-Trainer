"""Publication completeness and ranked scope membership are different facts."""
from copy import deepcopy

import pytest

from sm64_events.server.player_comparison import compare
from sm64_events.community.progress_snapshot import project
from test_progress_snapshot import pb, seed_definitions, service


def owner_with_private_segment():
    definition = deepcopy(seed_definitions()[0])
    definition.update(id=7,seed_key=None,name="Private practice",category="Custom")
    routes = [{"id":1,"name":"Stars","category":"Main Categories",
               "steps":[{"need":1,"candidates":[{"type":"star","course":1,"star":0}]}]},
              {"id":2,"name":"Private segment","category":"Main Categories",
               "steps":[{"need":1,"candidates":[{"type":"segment","segment_id":7}]}]}]
    owner = service([pb(),pb(2,segment_id=7,course_id=None,star_id=None,timer_mode="rta")],
                    definitions=[definition],routes=routes)
    original_curve,original_ladders = owner.ranks.overall_curve,owner.ranks.ladders
    owner.ranks.overall_curve = lambda key,version=None: original_curve("star:1:0",version)
    owner.ranks.ladders = lambda key,version=None: original_ladders("star:1:0",version)
    owner.ranks.graded_entities = lambda: {"star:1:0","segment:7"}
    owner.active_route = lambda: None
    return owner


@pytest.mark.parametrize("scope",["course:1","route:1"])
def test_real_projection_withholds_private_segment_but_preserves_complete_star_scope(scope):
    owner = owner_with_private_segment()
    content = project(owner)
    assert content["coverage"] == {"total_rows":2,"shared_rows":1,"withheld_rows":1,
                                    "unsupported_rows":0,"complete":False}
    result = compare(owner,content,scope)
    assert not result["coverage"]["complete"] and result["coverage"]["scope_complete"]
    assert result["player_rank"]["marelo"] == result["you_rank"]["marelo"]
    assert result["rank_reason"] is None
    assert [chip["scope_id"] for chip in result["chips"]] == ["route:1"]


@pytest.mark.parametrize("scope",["overall","route:2"])
def test_rank_stays_unavailable_when_a_withheld_segment_can_affect_the_scope(scope):
    owner = owner_with_private_segment()
    result = compare(owner,project(owner),scope)
    assert not result["coverage"]["scope_complete"]
    assert result["player_rank"] is None and result["rank_reason"]
    assert [chip["scope_id"] for chip in result["chips"]] == ["route:1"]


def test_excluded_segment_cannot_suppress_otherwise_complete_overall_rank():
    owner = owner_with_private_segment()
    owner.rank_excluded = lambda: {"segment:7"}
    result = compare(owner,project(owner),"overall")
    assert result["coverage"]["scope_complete"]
    assert result["player_rank"]["marelo"] == result["you_rank"]["marelo"]


def test_unsupported_rows_keep_unknown_facts_out_of_a_complete_star_rank():
    owner = owner_with_private_segment()
    content = project(owner)
    content["coverage"].update(total_rows=3,unsupported_rows=1)
    result = compare(owner,content,"course:1")
    assert result["player_rank"] is None and result["chips"] == []
    assert not result["coverage"]["scope_complete"]


def test_canonical_public_segment_members_are_not_hidden_by_unrelated_private_rows():
    owner = owner_with_private_segment()
    public = deepcopy(seed_definitions()[1])
    public["id"] = 8
    owner.db.definitions.append(public)
    owner.ranks.graded_entities = lambda: {"star:1:0","segment:7","segment:8"}
    owner.rank_excluded = lambda: {"segment:7"}
    result = compare(owner,project(owner),"overall")
    assert not result["coverage"]["complete"] and result["coverage"]["scope_complete"]
    assert result["player_rank"]["n"] == 2
    assert result["player_rank"]["marelo"] == result["you_rank"]["marelo"]


def test_an_ambiguous_unbound_public_segment_blocks_only_its_actual_scope_members():
    owner = owner_with_private_segment()
    public = deepcopy(seed_definitions()[1])
    public["id"] = 7
    owner.db.definitions = [public]
    content = project(owner)
    duplicate = {**deepcopy(public),"id":8}
    owner.db.definitions.append(duplicate)
    result = compare(owner,content,"overall")
    assert result["coverage"]["unbound_rows"] == 1
    assert result["player_rank"] is None and not result["coverage"]["scope_complete"]
    star_only = compare(owner,content,"route:1")
    assert star_only["player_rank"] and star_only["coverage"]["scope_complete"]
