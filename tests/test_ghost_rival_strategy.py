"""Current-strategy boundaries and the reported legacy Wild Blue PB."""
from dataclasses import replace
import json
from types import SimpleNamespace

import pytest

from sm64_events.core.paths import bundled_rank_standards, bundled_sheet_ladders
from sm64_events.ghost.adaptive import PracticeIdentity
from sm64_events.ghost.pb import AttemptGhost
from sm64_events.ghost.population import LocalPopulation
from sm64_events.ghost.rivals import Rivals, STATE_KEY
from sm64_events.ranks.standards import RankStandards
from sm64_events.storage.db import Database
from tests.test_ghost_adaptive import attempt, basis
from tests.test_ghost_population import recorded, admit
from tests.test_ghost_rivals import CONTROLS, TARGET, Native, Population, controller


def test_strategy_filter_precedes_search_page_limit_and_native_resolution(tmp_path):
    population = LocalPopulation(tmp_path/'population')
    for n in range(22):
        row,replay,_ = recorded(tmp_path/str(n),number=n+1,strategy='Other' if n<21 else 'Standard',frames=n+2)
        admit(population,row,replay,player=f'p{n}',name='FormFrame')
    identity = PracticeIdentity('star','2-1','igt','us','Standard')
    result = population.search(identity,'Form',limit=20)
    assert len(result['records'])==1 and result['next_cursor'] is None
    assert result['records'][0]['strategy']=='Standard'
    assert len(population.metadata_snapshot('star','2-1',clock='igt',game_version='us',strategy='Standard'))==1
    with pytest.raises(ValueError,match='target'):
        population.resolve(result['records'][0]['ghost'],{**TARGET,'strat':'Other'})
    population.close()


def test_strategy_switch_restores_only_its_own_pins_and_migrates_legacy_choices(tmp_path,monkeypatch):
    rivals,tracker,native,population,_ = controller(tmp_path,monkeypatch)
    standard = population.rows[0]
    different = {**population.rows[1], 'strategy':'Different'}
    population.rows[1]=different
    monkeypatch.setattr('sm64_events.ghost.rivals.saved_pb_basis',
        lambda _db,_ranks,identity:basis(step=10,frames=1200,current_strategy=identity.strategy))
    legacy = json.dumps(['star','2-1'],separators=(',',':'))
    tracker.db.set_state(STATE_KEY,{'version':1,'options':{**rivals.options,'strategy_only':False},
        'pins':{legacy:{'0':standard['id'],'1':different['id']}}})
    restored = Rivals(tracker,population,native,lambda:'us')
    restored.refresh(TARGET,CONTROLS,enabled=True)
    assert restored.pins[restored._key(TARGET)]=={'0':standard['id']}
    other = {**TARGET,'strat':'Different'}
    assert restored.pins[restored._key(other)]=={'1':different['id']}
    restored.refresh(other,CONTROLS,enabled=True)
    assert all(row['strategy']=='Different' for row in restored.rows)
    restored.refresh(TARGET,CONTROLS,enabled=True)
    assert any(row['id']==standard['id'] and row['pinned'] for row in restored.rows)
    assert 'strategy_only' not in restored.options


def test_private_library_cannot_replace_current_strategy_with_another(tmp_path,monkeypatch):
    rivals,tracker,_,_,_ = controller(tmp_path,monkeypatch)
    run = attempt(strat_tag='Different')
    tracker.db.upsert_attempt(run)
    before = json.dumps(rivals.pins),dict(rivals.local_choices)
    with pytest.raises(ValueError,match='strategy'):
        rivals.choose_library(TARGET,[AttemptGhost(1,'star','2-1','Different',600)],True)
    assert (json.dumps(rivals.pins),rivals.local_choices)==before


def reported_wild_blue_standards(ranks):
    """Resolved Standard cutoffs captured from live /api/ranks/standards Oct8 UTC.

    This exercises scoring from cutoffs, never a directly assigned PB rank.
    The live library fit differs from the bundled seed alone.
    """
    ladder={'Mario':9.9,'Grandmaster':10.06,'Master':10.23,'Diamond':10.4,
            'Platinum':10.63,'Gold':10.96,'Silver':11.2,'Bronze':21.93}
    for tier,seconds in ladder.items():
        ranks.set_threshold('star:2:2','Standard',tier,seconds)
    return ranks


def wild_blue_roster(tmp_path):
    ranks = RankStandards(tmp_path/'ranks.json',seed_path=bundled_rank_standards(),sheet_path=bundled_sheet_ladders())
    ranks.load()
    reported_wild_blue_standards(ranks)
    ranks.grading_version = 'us'
    db = Database(tmp_path/'practice.db')
    # The reported live attempt has exact poses/clock but predates ROM attribution.
    run = replace(attempt(frames=321),id=13194,course_id=2,star_id=2,game_version=None,
        native_clock_frame=30978,closed_frame=31297,native_clock_kind='star',timed_at='xcam')
    db.upsert_attempt(run)
    db.insert_pb(2,2,'Standard','igt',321,run.id,'2026-10-06T21:31:41Z',game_version=None)
    tracker = SimpleNamespace(db=db,ranks=ranks)
    population, native = Population(),Native()
    population.rows=[]
    rivals = Rivals(tracker,population,native,lambda:'us')
    target = {'kind':'star','entity_key':'2-2','strat':'Standard'}
    source = rivals.refresh(target,CONTROLS,enabled=True)
    return rivals,source,native,tracker,target


def test_reported_null_rom_pb_remains_in_roster_without_community_rivals(tmp_path):
    rivals,source,native,_,_ = wild_blue_roster(tmp_path)
    assert rivals.pb['frames']==321 and rivals.pb['step']==19
    assert (rivals.pb['tier'],rivals.pb['division'])==('Gold','I')
    assert not rivals.rows and source['available']
    assert len(native.preparations[-1][1])==1 and native.preparations[-1][1][0].pb
    assert rivals.payload()['pb']['frames']==321


@pytest.mark.parametrize('baseline',[None,[],'invalid'])
def test_malformed_simulation_baseline_cannot_break_local_roster(tmp_path,monkeypatch,baseline):
    rivals,tracker,_,_,_ = controller(tmp_path,monkeypatch)
    monkeypatch.setenv('SM64_PRACTICE_ROOT',str(tmp_path.resolve()))
    tracker.db.set_state('ghost_simulation_v1',{'version':1,'scenario_root':str(tmp_path.resolve()),
        'target':TARGET,'baseline':baseline})
    rivals.refresh(TARGET,CONTROLS,enabled=True,force=True)
    assert rivals.pb['frames']==1200


def test_legacy_migration_ignores_invalid_ids_and_preserves_destination_collisions(tmp_path,monkeypatch):
    rivals,tracker,native,population,_ = controller(tmp_path,monkeypatch)
    first,second,third=(row['id'] for row in population.rows[:3])
    legacy=rivals._entity_key(TARGET)
    tracker.db.set_state(STATE_KEY,{'options':rivals.options,'pins':{
        legacy:{'0':first,'1':second,'2':third,'3':'z'*64},
        rivals._key(TARGET):{'0':second,'1':first}},
        'local_choices':{third:{'attempt_id':[]}}})
    restored=Rivals(tracker,population,native,lambda:'us')
    restored.refresh(TARGET,CONTROLS,enabled=True)
    choices=restored.pins[restored._key(TARGET)]
    assert choices['0']==second and choices['1']==first
    assert set(choices.values())=={first,second,third}
    assert len(choices)==len(set(choices.values()))


@pytest.mark.parametrize('dedicated',[None,AttemptGhost(1,'star','2-1','Standard',600,pb=True)])
def test_library_pb_label_cannot_hide_a_different_playable_owner(tmp_path,monkeypatch,dedicated):
    rivals,tracker,_,_,_ = controller(tmp_path,monkeypatch)
    run=attempt(2,frames=650)
    tracker.db.upsert_attempt(run)
    monkeypatch.setattr('sm64_events.ghost.rivals.saved_pb_basis',lambda *_:basis(ghost=dedicated))
    rivals.choose_library(TARGET,[AttemptGhost(2,'star','2-1','Standard',650,pb=True)],True)
    assert len(rivals.pins[rivals._key(TARGET)])==1
    assert next(iter(rivals.local_choices.values()))['attempt_id']==2


def test_simulated_portable_baseline_yields_to_played_pb_and_undo_restores_it(tmp_path,monkeypatch):
    from tests.test_ghost_climb_simulation import donors
    from tools.ghost_climb import simulate_wild_blue
    from sm64_events.ghost.native_session import NativeGhost
    from pathlib import Path
    document=simulate_wild_blue(donors(tmp_path,timings=(337,332,331,329,328,327,326,324,323,321,320)),tmp_path/'profiles')
    profile=Path(document['profile'])
    monkeypatch.setenv('SM64_PRACTICE_ROOT',str(profile))
    db=Database(profile/'data/tracker.db')
    ranks=RankStandards(tmp_path/'ranks.json',bundled_rank_standards(),bundled_sheet_ladders())
    ranks.load()
    reported_wild_blue_standards(ranks)
    population=LocalPopulation(document['population'])
    native=NativeGhost(SimpleNamespace(recorder=None))
    rivals=Rivals(SimpleNamespace(db=db,ranks=ranks),population,native,lambda:'us')
    target={'kind':'star','entity_key':'2-2','strat':'Standard'}
    try:
        source=rivals.refresh(target,CONTROLS,enabled=True)
        assert source['available'] and rivals.pb['ghost'].import_id
        # Round 49 (2026-10-13): the range above step 9 seats two rivals, and
        # the remaining seats widen upward to the nearest subdivision above it.
        assert rivals.pb['step']==9 and [row['step'] for row in rivals.rows]==[12,13,15,15]
        assert rivals.payload()['simulation']=='Developer simulation · Wild Blue'
        run=attempt(1000,frames=331,star_id=2)
        db.upsert_attempt(run)
        played=db.insert_pb(2,2,'Standard','igt',331,run.id,'2026-10-08T00:00:00Z',game_version='us')
        rivals.refresh(target,CONTROLS,enabled=False,force=True)
        assert rivals.pb['attempt_id']==1000 and not getattr(rivals.pb['ghost'],'import_id',None)
        assert len(rivals.rows)==4
        db.delete_pb(played)
        rivals.refresh(target,CONTROLS,enabled=False,force=True)
        assert rivals.pb['frames']==337 and rivals.pb['ghost'].import_id
    finally:
        population.close()
        db.close()
