"""One Practice scope drives pool, protected choices, search and native ownership."""
from dataclasses import replace
import json
from types import SimpleNamespace

import pytest

from sm64_events.ghost.adaptive import PracticeIdentity, rank_frames
from sm64_events.ghost.population import LocalPopulation
from sm64_events.ghost.rivals import Rivals, STATE_KEY
from tests.test_ghost_population import recorded, admit
from tests.test_ghost_rivals import controller, TARGET, CONTROLS
from tests.test_ghost_rival_strategy import wild_blue_roster


def switch(rivals,scope):
    rivals.apply(rivals.checked({'scope':scope},rivals.target))
    return rivals.refresh(rivals.target,CONTROLS,enabled=True)


def test_existing_strategy_choices_survive_overall_switch_and_restart(tmp_path,monkeypatch):
    rivals,tracker,native,population,_=controller(tmp_path,monkeypatch)
    population.rows[-1]={**population.rows[-1],'strategy':'Other'}
    chosen=population.rows[1]['id']
    rivals.pins[rivals._key(TARGET)]={'0':chosen}
    rivals.refresh(TARGET,CONTROLS,enabled=True,force=True)
    strategy_key=rivals._key(TARGET)
    assert rivals.payload()['scope']=='strategy'
    assert all(row['strategy']=='Standard' for row in rivals.rows)
    switch(rivals,'overall')
    overall_key=rivals._key(TARGET)
    assert overall_key!=strategy_key and rivals.pins[strategy_key]=={'0':chosen}
    assert native.preparations[-1][0]['scope']=='overall'
    rivals.pins[overall_key]={'1':population.rows[-1]['id']}
    rivals._store()
    rivals.refresh(TARGET,CONTROLS,enabled=True,force=True)
    assert any(row['strategy']=='Other' and row['pinned'] for row in rivals.rows)
    restored=Rivals(tracker,population,native,lambda:'us')
    restored.refresh(TARGET,CONTROLS,enabled=True)
    assert restored.payload()['scope']=='overall'
    assert restored.pins[overall_key]==rivals.pins[overall_key]
    switch(restored,'strategy')
    assert any(row['id']==chosen and row['pinned'] for row in restored.rows)
    assert restored.pins[overall_key]=={'1':population.rows[-1]['id']}


@pytest.mark.parametrize('scope',[None,True,3,[],{},'all','OVERALL'])
def test_invalid_scope_is_an_atomic_refusal(tmp_path,monkeypatch,scope):
    rivals,tracker,_,_,_=controller(tmp_path,monkeypatch)
    before=json.dumps(tracker.db.get_state(STATE_KEY,{}),sort_keys=True)
    with pytest.raises(ValueError,match='Strategy or Overall'):
        rivals.checked({'scope':scope},TARGET)
    assert before==json.dumps(tracker.db.get_state(STATE_KEY,{}),sort_keys=True)
    assert rivals.payload()['scope']=='strategy'


def test_stale_scope_search_actions_and_drafts_cannot_change_another_view(tmp_path,monkeypatch):
    rivals,_,_,population,_=controller(tmp_path,monkeypatch)
    switch(rivals,'overall')
    before=json.dumps(rivals.pins,sort_keys=True),dict(rivals.options)
    with pytest.raises(ValueError,match='ghost view changed'):
        rivals.search(TARGET,'Form',scope='strategy')
    for body in ({'action':'reset','slot':0},{'options':rivals.options},
                 {'action':'add','id':population.rows[-1]['id']}):
        with pytest.raises(ValueError,match='ghost view changed'):
            rivals.checked({**body,'scope':'strategy'},TARGET)
    assert before==(json.dumps(rivals.pins,sort_keys=True),rivals.options)
    assert rivals.search(TARGET,'Form',scope='overall')['records']


def test_scope_is_per_entity_and_legacy_pins_migrate_to_strategy_even_in_overall(tmp_path,monkeypatch):
    rivals,tracker,native,population,_=controller(tmp_path,monkeypatch)
    legacy=rivals._entity_key(TARGET)
    tracker.db.set_state(STATE_KEY,{'version':1,'options':rivals.options,
        'scopes':{legacy:'overall'},'pins':{legacy:{'2':population.rows[-1]['id']}}})
    restored=Rivals(tracker,population,native,lambda:'us')
    restored.refresh(TARGET,CONTROLS,enabled=True)
    assert restored.pins[restored._strategy_key(TARGET)]=={'2':population.rows[-1]['id']}
    assert not restored.pins.get(restored._key(TARGET))
    other={**TARGET,'entity_key':'2-2'}
    assert restored.scope_for(other)=='strategy'


def test_overall_search_and_native_resolution_allow_other_strategy_only_in_that_scope(tmp_path):
    population=LocalPopulation(tmp_path/'population')
    for n,strategy in enumerate(('Standard','Other')):
        row,replay,_=recorded(tmp_path/str(n),number=n+1,strategy=strategy,frames=n+2)
        admit(population,row,replay,player=f'p{n}',name='FormFrame')
    identity=PracticeIdentity('star','2-1','igt','us','Standard')
    try:
        strategy=population.search(identity,'Form')['records']
        overall=population.search(replace(identity,scope='overall'),'Form')['records']
        assert [row['strategy'] for row in strategy]==['Standard']
        assert {row['strategy'] for row in overall}=={'Standard','Other'}
        other=next(row for row in overall if row['strategy']=='Other')
        path,metadata=population.resolve(other['ghost'],{**TARGET,'scope':'overall'})
        assert path.is_file() and metadata['target']['strat']=='Other'
        with pytest.raises(ValueError):
            population.resolve(other['ghost'],{**TARGET,'scope':'strategy'})
    finally:
        population.close()


def test_saved_legacy_pb_keeps_owner_and_changes_only_its_scoring_scope(tmp_path):
    rivals,_,native,tracker,target=wild_blue_roster(tmp_path)
    strategy_pb=dict(rivals.pb)
    try:
        switch(rivals,'overall')
        expected=rank_frames(tracker.ranks,rivals.identity,321,version=None)
        assert all(rivals.pb[key]==expected[key] for key in ('step','tier','division'))
        assert rivals.pb['attempt_id']==strategy_pb['attempt_id']==13194
        assert rivals.pb['frames']==strategy_pb['frames']==321
        assert native.preparations[-1][0]=={**target,'scope':'overall'}
        switch(rivals,'strategy')
        assert rivals.pb['step']==19
        assert native.preparations[-1][0]=={**target,'scope':'strategy'}
    finally:
        tracker.db.close()


def test_scope_switch_after_pb_change_reconciles_the_new_native_owner(tmp_path,monkeypatch):
    from sm64_events.ghost.session import GhostSession
    rivals,tracker,_,_,_=controller(tmp_path,monkeypatch)
    rivals.pb_changed()
    answer=switch(rivals,'overall')
    assert answer is not None and answer.get('reason')!='pb_settling' and rivals.pb is not None
    commands=[]
    session=GhostSession(tracker=tracker,inputs=None,replay=None,broadcaster=None,
        native=SimpleNamespace(reconcile=lambda **body:commands.append(body),state=lambda:{}))
    session._rivals,session._target=rivals,TARGET
    session._values['enabled']=True
    session._reconcile()
    assert commands[-1]['target']=={**TARGET,'scope':'overall'}
    assert session.payload()['target']==TARGET


def test_search_api_validates_scope_and_keeps_legacy_call_compatible():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sm64_events.server.ghost_api import create_ghost_router
    calls=[]
    class Session:
        async def search_rivals(self,target,player,cursor=None,*,scope=None):
            calls.append((target,player,cursor,scope))
            return {'records':[],'next_cursor':None}
    app=FastAPI()
    app.include_router(create_ghost_router(Session()))
    with TestClient(app) as client:
        query={'target':json.dumps(TARGET),'player':'FormFrame'}
        assert client.get('/api/ghost/rivals/search',params=query).status_code==200
        assert calls[-1][-1] is None
        assert client.get('/api/ghost/rivals/search',params={**query,'scope':'overall'}).status_code==200
        assert calls[-1][-1]=='overall'
        before=len(calls)
        assert client.get('/api/ghost/rivals/search',params={**query,'scope':'invalid'}).status_code==422
        assert len(calls)==before


def test_other_strategy_library_refusal_does_not_persist_or_poison_the_session(tmp_path):
    import asyncio
    from sm64_events.ghost.selection import SELECTION_KEY
    from sm64_events.storage.db import Database
    from tests.test_ghost_session import a_session,mark
    from tests.test_ghost_adaptive import Standards,attempt
    from tests.test_ghost_rivals import Population
    db=Database(tmp_path/'practice.db')
    session,native,tracker,store=a_session(tmp_path,target=('star',2,1),strat='Standard',db=db)
    tracker.ranks=Standards()
    db.upsert_attempt(attempt(7,strat_tag='Other'))
    mark(store,entity_key='2-1',strat='Other')
    population = Population()
    # This case exercises refusing a manual strategy change, not population
    # playback. Its pure-selection row stubs have no native clip templates.
    population.rows = []
    session._rivals=Rivals(tracker,population,native,lambda:'us')
    async def check():
        before=await session.refresh()
        stored=db.get_state(SELECTION_KEY,None)
        with pytest.raises(ValueError,match='strategy'):
            await session.update({'target':TARGET,'ghosts':{'pb':False,'attempts':[7]},
                                  'enabled':True,'opacity':.2})
        assert session._pending=={}
        after=await session.refresh()
        assert after['opacity']==before['opacity'] and after['enabled']==before['enabled']
        assert after['selection']==before['selection']
        assert db.get_state(SELECTION_KEY,None)==stored
        await session.stop()
    try:
        asyncio.run(check())
    finally:
        db.close()
