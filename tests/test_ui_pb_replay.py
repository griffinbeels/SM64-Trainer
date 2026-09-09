"""Mount the actual attempt row and exercise separate PB/media outcomes."""
import pytest

from test_ui_replay_picture_steps import PROJECT, get_driver


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
