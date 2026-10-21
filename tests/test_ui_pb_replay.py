"""Mount the actual attempt row and exercise separate PB/media outcomes."""
from dataclasses import replace
import json
import pytest

from test_ui_replay_picture_steps import PROJECT, get_driver
from test_pbaction import BASE
from sm64_events.tracking.views import _attempt_json


@pytest.mark.parametrize("width", [850, 1500])
def test_equal_and_slower_pb_actions_are_disabled_but_faster_still_saves(width, tmp_path):
    pb = {"attempt_id": 999, "frames": 515}
    rows = [_attempt_json(replace(BASE, id=n, igt_frames=frames), "igt", active_strat="Standard",
                          pbs_by_strat={(8, 1, "igt", "Standard"): pb})
            for n, frames in ((42, 533), (43, 515), (44, 500))]
    with PROJECT.open() as url, get_driver().launch(viewport=(width, 950)) as page:
        page.goto(url)
        page.wait_for(PROJECT.ready_selector)
        page.evaluate("window.pbAttempts=" + json.dumps(rows))
        page.evaluate(r"""(async () => {
          const [{h,render},{AttemptRow}]=await Promise.all([
            import('preact'),import('/ui/components/attemptlog.js')]);
          const original=window.fetch;
          window.pbRequests=[];window.pbErrors=[];
          window.addEventListener('error',e=>window.pbErrors.push(e.message));
          window.addEventListener('unhandledrejection',e=>window.pbErrors.push(String(e.reason)));
          window.fetch=async(url,options)=>{
            if(String(url)==='/api/pb'){
              window.pbRequests.push(JSON.parse(options.body));
              return new Response('{}',{status:200,headers:{'Content-Type':'application/json'}});
            }
            return original(url,options);
          };
          const host=document.createElement('div');host.id='pb-improvements';document.body.replaceChildren(host);
          render(h('table',{class:'attempt-table'},h('tbody',null,...window.pbAttempts.map((a,idx)=>
            h(AttemptRow,{a,idx,t:{clock:'igt',view:{catalog:{}},refresh:async()=>{}}})))),host);
        })()""")
        page.wait_for("#pb-improvements tr:nth-child(3) button:has-text('Save as PB')")
        states = json.loads(page.evaluate("JSON.stringify([...document.querySelectorAll('#pb-improvements button')].filter(b=>b.querySelector('.save-pb-wide')).map(b=>({disabled:b.disabled,title:b.title})))"))
        assert [row["disabled"] for row in states] == [True, True, False]
        assert all("doesn't beat your PB" in row["title"] and "Save as ghost" in row["title"] for row in states[:2])
        page.evaluate("document.querySelectorAll('#pb-improvements .pb-unavailable').forEach(b=>b.click())")
        assert page.evaluate("window.pbRequests.length") == 0
        page.click("#pb-improvements tr:nth-child(3) button:has-text('Save as PB')")
        page.wait_for("#pb-improvements tr:nth-child(3) button:has-text('Save as PB'):not(:disabled)")
        assert json.loads(page.evaluate("JSON.stringify(window.pbRequests)")) == [{"attempt_id": 44, "timer_mode": "igt"}]
        assert page.evaluate("window.pbErrors") == []
        (tmp_path / f"pb-improvement-{width}.png").write_bytes(page.screenshot())


@pytest.mark.parametrize("width", [850, 1500])
def test_manual_pb_failure_and_media_retry_in_real_row(width, tmp_path):
    with PROJECT.open() as url, get_driver().launch(viewport=(width, 950)) as page:
        page.goto(url)
        page.wait_for(PROJECT.ready_selector)
        page.evaluate(r"""(async () => {
          const [{h,render},{AttemptRow}]=await Promise.all([
            import('preact'), import('/ui/components/attemptlog.js')]);
          const original=window.fetch;
          window.pbRequests=[]; window.pbErrors=[];
          window.addEventListener('error', e=>window.pbErrors.push(e.message));
          window.addEventListener('unhandledrejection', e=>window.pbErrors.push(String(e.reason)));
          window.fetch=async (url,options)=>{
            const path=String(url);
            if(path==='/api/pb' || path==='/api/attempts/42/replay/save') {
              window.pbRequests.push({path,body:options?.body});
              const result=path==='/api/pb'
                ? {replay_save:{status:'failed',message:'Not enough free storage. Free space and retry.'}}
                : {path:'saved.mp4',truncated:false};
              return new Response(JSON.stringify(result),{status:200,headers:{'Content-Type':'application/json'}});
            }
            return original(url,options);
          };
          const host=document.createElement('div');host.id='pb-row-witness';
          document.body.replaceChildren(host);
          const attempt={id:42,outcome:'success',igt:'33"83',igt_frames:1015,
            rta:'34"00',rta_frames:1020,pb_action:'save',pb_delta_frames:null,strat_tag:'Standard'};
          function draw(){render(h('table',{class:'attempt-table'},h('tbody',null,
            h(AttemptRow,{a:attempt,idx:0,t:{clock:'igt',view:{catalog:{}},refresh:async()=>{
              attempt.pb_action='undo'; draw();
            }}}))),host);}
          draw();
        })()""")
        page.click('#pb-row-witness button:has-text("Save as PB")')
        page.wait_for('.pb-replay-notice:has-text("PB saved. Replay could not be saved")')
        assert page.evaluate("window.pbRequests.length") == 1
        (tmp_path / f"pb-video-failure-{width}.png").write_bytes(page.screenshot())
        page.click('.pb-replay-notice button:text-is("Retry save replay")')
        page.wait_for('.pb-replay-notice:has-text("Replay saved.")')
        assert page.evaluate("window.pbRequests.map(r=>r.path)") == [
            "/api/pb", "/api/attempts/42/replay/save"]
        assert page.evaluate("window.pbErrors") == []
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
