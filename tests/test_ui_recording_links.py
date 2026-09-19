"""Recording interactions in the shipped Preact components.

Only HTTP boundaries are replaced: these tests drive the real editor, replay
selection, and Library player without downloading public videos in the suite.
"""
import base64
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))
from find_uilab import find_uilab  # noqa: E402

_MISSING = find_uilab()
if _MISSING:
    pytest.skip(_MISSING, allow_module_level=True)

from ui_fixture import serve_ui  # noqa: E402
from uilab import driver  # noqa: E402


@pytest.fixture(scope="module")
def server():
    with serve_ui() as base:
        yield base


@pytest.fixture
def page(server):
    with driver.get_driver().launch(headless=True, viewport=(850, 900)) as browser:
        browser.goto(f"{server}/ui/index.html")
        browser.wait_for(".log-list-card")
        browser.evaluate("""(() => {
          window.recording = {url: null, revision: 0};
          window.calls = []; window.failSave = false; window.conflict = false;
          const original = window.fetch;
          window.fetch = async (url, options = {}) => {
            const path = new URL(url, location.href).pathname;
            const method = options.method || 'GET';
            const reply = (body, status = 200) => new Response(JSON.stringify(body),
              {status, headers: {'Content-Type': 'application/json'}});
            if (path === '/api/attempts/4242/recording') {
              calls.push({path, method, body: options.body && JSON.parse(options.body)});
              if (method === 'PUT') {
                if (window.failSave) return reply({detail: 'Please retry saving'}, 503);
                if (window.conflict) {
                  window.conflict = false;
                  window.recording = {url: 'https://recording.example/other', revision: 9};
                  return reply({detail: 'changed'}, 409);
                }
                const body = JSON.parse(options.body);
                if (body.expected_revision !== recording.revision)
                  return reply({detail: 'changed'}, 409);
                window.recording = {url: body.url, revision: recording.revision + 1};
              }
              return reply(recording);
            }
            if (path.startsWith('/api/media')) {
              calls.push({path, method, body: options.body && JSON.parse(options.body)});
              if (path === '/api/media/preview')
                return reply({title: 'My recording', site: 'Recording', thumbnail: null});
              if (window.mediaReply) return reply(window.mediaReply);
              return reply(method === 'POST'
                ? {state: 'error', error: 'Offline', start_s: 0}
                : {state: 'missing', start_s: 0});
            }
            if (path === '/api/attempts/4242/replay') {
              calls.push({path, method});
              return reply({clip_url: '/ui/assets/empty/ukiki_1.png', game_fps: 30});
            }
            return original(url, options);
          };
          const root = document.createElement('div'); root.id = 'recording-test';
          root.style.cssText = 'max-width:620px;margin:20px;padding:16px;background:#101f31';
          document.body.prepend(root);
        })()""")
        yield browser


def mount(page, *, url=None, imported=True, library=False):
    page.evaluate(f"window.recording = {{url: {json.dumps(url)}, revision: 0}}")
    page.evaluate(f"""(async () => {{
      const {{h, render}} = await import('preact');
      const mod = await import('/ui/components/{'librarytarget' if library else 'replay'}.js');
      render(h(mod.{'ExampleMedia' if library else 'ReplayPlayer'},
        {json.dumps({'entry': {'video': url, 'runner': 'Runner', 'time_cs': 1133}} if library else {'attemptId': 4242, 'imported': imported})}),
        document.querySelector('#recording-test'));
    }})()""")
    page.wait_for("#recording-test .external-video" if library else "#recording-test .recording-link")
    wait(page, "document.querySelector('#recording-test input') || "
         "document.querySelector('#recording-test .recording-link-preview')" if not library
         else "document.querySelector('#recording-test button')")


def wait(page, predicate):
    assert page.evaluate(f"""(async () => {{
      const deadline = Date.now() + 6000;
      while (Date.now() < deadline) {{
        if ({predicate}) return true;
        await new Promise(resolve => setTimeout(resolve, 30));
      }} return false;
    }})()"""), predicate


def click(page, label):
    assert page.evaluate(f"""(() => {{
      const button = [...document.querySelectorAll('#recording-test button')]
        .find(item => item.textContent.trim() === {json.dumps(label)});
      button?.click(); return !!button;
    }})()"""), label


def draft(page, value):
    page.evaluate(f"""(() => {{
      const input = document.querySelector('#recording-test input');
      input.value = {json.dumps(value)};
      input.dispatchEvent(new Event('input', {{bubbles: true}}));
    }})()""")
    page.wait_ms(50)


def test_add_preview_save_and_persistent_undo_never_download(page):
    mount(page)
    draft(page, '  https://recording.example/video?t=30&list=one  ')
    wait(page, "calls.some(call => call.path === '/api/media/preview')")
    assert page.evaluate("recording.url") is None
    click(page, "Add link")
    wait(page, "recording.revision === 1")
    assert page.evaluate("recording.url") == 'https://recording.example/video?t=30&list=one'
    wait(page, "document.querySelector('#recording-test .recording-link-status').textContent.includes('Link added')")
    assert page.evaluate("calls.filter(call => call.method === 'POST').length") == 0
    click(page, "Undo")
    wait(page, "recording.revision === 2")
    assert page.evaluate("recording.url") is None
    assert page.evaluate("calls.filter(call => call.path.endsWith('/replay')).length") == 0


def test_change_focus_cancel_remove_and_undo(page):
    original = 'https://recording.example/original?t=12'
    mount(page, url=original)
    click(page, "Change link")
    wait(page, "document.activeElement === document.querySelector('#recording-test input')")
    assert page.evaluate("document.activeElement.selectionEnd - document.activeElement.selectionStart") == len(original)
    draft(page, 'https://recording.example/wrong')
    page.evaluate("document.querySelector('#recording-test input').dispatchEvent("
                  "new KeyboardEvent('keydown', {key:'Escape', bubbles:true}))")
    wait(page, "!document.querySelector('#recording-test input')")
    assert page.evaluate("recording.url") == original
    click(page, "Change link")
    page.wait_for("#recording-test input")
    click(page, "Remove link")
    wait(page, "recording.url === null")
    click(page, "Undo")
    wait(page, "recording.revision === 2")
    assert page.evaluate("recording.url") == original


def test_failed_save_retains_draft_and_saved_link_then_retries(page):
    mount(page, url='https://recording.example/original')
    click(page, "Change link")
    page.wait_for("#recording-test input")
    draft(page, 'https://recording.example/replacement')
    page.evaluate("window.failSave = true")
    click(page, "Save link")
    page.wait_for("#recording-test .recording-link-error")
    assert page.evaluate("document.querySelector('#recording-test input').value") == 'https://recording.example/replacement'
    assert page.evaluate("recording.url") == 'https://recording.example/original'
    page.evaluate("window.failSave = false")
    click(page, "Retry")
    wait(page, "recording.revision === 1")
    assert page.evaluate("recording.url") == 'https://recording.example/replacement'


def test_revision_conflict_preserves_draft_until_deliberate_retry(page):
    mount(page)
    draft(page, 'https://recording.example/mine')
    page.evaluate("window.conflict = true")
    click(page, "Add link")
    page.wait_for("#recording-test .recording-link-error")
    assert page.evaluate("document.querySelector('#recording-test input').value") == 'https://recording.example/mine'
    assert page.evaluate("recording.url") == 'https://recording.example/other'
    click(page, "Retry")
    wait(page, "recording.revision === 10")
    assert page.evaluate("recording.url") == 'https://recording.example/mine'


def test_undo_conflict_retries_the_original_restoration(page):
    original = 'https://recording.example/original'
    mount(page, url=original)
    click(page, "Change link")
    page.wait_for("#recording-test input")
    draft(page, 'https://recording.example/new')
    click(page, "Save link")
    wait(page, "recording.revision === 1")
    page.evaluate("window.conflict = true")
    click(page, "Undo")
    page.wait_for("#recording-test .recording-link-error")
    assert page.evaluate("document.querySelector('#recording-test input').value") == original
    click(page, "Retry")
    wait(page, "recording.revision === 10")
    assert page.evaluate("recording.url") == original
    wait(page, "document.querySelector('#recording-test .recording-link-status').textContent.includes('Link restored')")


def test_failed_removal_retries_null_instead_of_validating_empty_draft(page):
    mount(page, url='https://recording.example/original')
    click(page, "Change link")
    page.wait_for("#recording-test input")
    page.evaluate("window.failSave = true")
    click(page, "Remove link")
    page.wait_for("#recording-test .recording-link-error")
    page.evaluate("window.failSave = false")
    click(page, "Retry")
    wait(page, "recording.url === null")


def test_malformed_link_keeps_input_and_narrow_editor_fits(page):
    mount(page)
    draft(page, 'javascript:alert(1)')
    click(page, "Add link")
    page.wait_for("#recording-test input[aria-invalid=true]")
    assert page.evaluate("calls.filter(call => call.method === 'PUT').length") == 0
    page.evaluate("document.querySelector('#recording-test').style.width = '285px'")
    page.wait_ms(100)
    assert page.evaluate("""(() => {
      const panel = document.querySelector('#recording-test');
      return [...panel.querySelectorAll('input,button')].every(el =>
        el.getBoundingClientRect().right <= panel.getBoundingClientRect().right);
    })()""")


@pytest.mark.parametrize("library", [False, True])
def test_playing_only_embeds_until_explicit_download(page, library):
    mount(page, url='https://recording.example/library', library=library)
    wait(page, "calls.some(call => call.path === '/api/media')")
    assert page.evaluate("calls.filter(call => call.method === 'POST').length") == 0
    if library:
        page.evaluate("document.querySelector('#recording-test button').click()")
    wait(page, "document.querySelector('#recording-test .external-video-actions').textContent.includes('Download to enable full replay features.')")
    assert page.evaluate("calls.filter(call => call.method === 'POST').length") == 0
    click(page, "Download")
    wait(page, "calls.some(call => call.method === 'POST')")
    assert page.evaluate("calls.filter(call => call.method === 'POST').length") == 1
    if library:
        assert page.evaluate("document.querySelector('#recording-test .external-video-actions a').href") == 'https://recording.example/library'
    click(page, "Retry download")
    wait(page, "calls.filter(call => call.method === 'POST').length === 2")
    assert page.evaluate("calls.filter(call => call.method === 'POST')[1].body.retry")


@pytest.mark.parametrize("library", [False, True])
def test_download_completion_automatically_replaces_provider(page, library):
    page.evaluate("window.mediaReply = {state:'running', start_s:0}")
    mount(page, url='https://recording.example/library', library=library)
    if library:
        page.evaluate("document.querySelector('#recording-test .external-video > button').click()")
    click(page, "Download")
    wait(page, "document.querySelector('#recording-test .external-video-actions').textContent.includes('Downloading')")
    # A cached file must be reusable after close/reopen. An empty MediaSource
    # is single-attachment and falsely triggers the player's error fallback.
    clip = base64.b64encode((REPO / 'tests/fixtures/recording-controls.mp4').read_bytes()).decode()
    page.evaluate(f"""window.mediaReply = {{state:'ready', start_s:0,
      clip_url:URL.createObjectURL(new Blob([
        Uint8Array.from(atob({json.dumps(clip)}), c => c.charCodeAt(0))
      ], {{type:'video/mp4'}}))}}""")
    page.wait_for("#recording-test .external-video-local video")
    wait(page, "document.querySelector('#recording-test video').readyState >= 1")
    assert page.evaluate("document.querySelector('#recording-test video').src.startsWith('blob:')")
    assert not page.evaluate("document.querySelector('#recording-test').textContent.includes('Play downloaded')")
    if library:
        click(page, "Close recording")
        wait(page, "document.querySelector('#recording-test .external-video > button')")
        page.evaluate("document.querySelector('#recording-test .external-video > button').click()")
        click(page, "Download")
        page.wait_for("#recording-test .external-video-local video")
        wait(page, "document.querySelector('#recording-test video').readyState >= 1")
        assert page.evaluate("document.querySelector('#recording-test video').src === mediaReply.clip_url")
    else:
        assert page.evaluate("document.querySelectorAll('#recording-test a').length") == 1
        assert page.evaluate("document.querySelector('#recording-test .recording-link a').href") == 'https://recording.example/library'


def test_failed_local_playback_returns_to_provider(page):
    page.evaluate("window.mediaReply = {state:'ready', start_s:0, "
                  "clip_url:URL.createObjectURL(new MediaSource())}")
    mount(page, url='https://recording.example/fallback')
    click(page, "Download")
    page.wait_for("#recording-test .external-video-local video")
    page.evaluate("document.querySelector('#recording-test video').dispatchEvent(new Event('error'))")
    wait(page, "!document.querySelector('#recording-test .external-video-local')")
    assert page.evaluate("document.querySelector('#recording-test .recording-link a').href") == 'https://recording.example/fallback'


def test_provider_fallback_uses_the_resolved_start_timestamp(page):
    page.evaluate("window.mediaReply = {state:'error', error:'offline', start_s:95}")
    mount(page, url='https://youtu.be/abc123XYZ_-#t=1m35s')
    page.wait_for("#recording-test iframe")
    assert 'start=95' in page.evaluate("document.querySelector('#recording-test iframe').src")


def test_native_capture_is_preferred_and_survives_link_edit(page):
    mount(page, url='https://recording.example/original', imported=False)
    page.wait_for("#recording-test .replay-player video")
    click(page, "Change link")
    page.wait_for("#recording-test input")
    draft(page, 'https://recording.example/replacement')
    click(page, "Save link")
    wait(page, "recording.revision === 1")
    assert page.evaluate("document.querySelectorAll('#recording-test .replay-player video').length") == 1
    assert page.evaluate("calls.filter(call => call.path === '/api/media' && call.method === 'POST').length") == 0


def test_imported_attempt_drawer_opens_recording_without_waiting_for_native_inputs(page):
    page.evaluate("""(async () => {
      const {h, render} = await import('preact');
      const {AttemptDrawer} = await import('/ui/components/attemptdrawer.js');
      render(h(AttemptDrawer, {attemptId:4242, imported:true}),
        document.querySelector('#recording-test'));
    })()""")
    page.wait_for('#recording-test .recording-link input')
    assert page.evaluate("document.querySelector('#recording-test .input-timeline-waiting')") is None
    assert page.evaluate("calls.filter(call => call.path.endsWith('/replay')).length") == 0
    assert page.evaluate("document.querySelector('#recording-test').textContent.includes('Add a public recording')")


def test_replacing_external_link_requires_new_download_gesture(page):
    mount(page, url='https://recording.example/old')
    click(page, "Download")
    wait(page, "calls.filter(call => call.method === 'POST').length === 1")
    click(page, "Change link")
    page.wait_for("#recording-test input")
    draft(page, 'https://recording.example/new')
    page.evaluate("document.querySelector('#recording-test input').dispatchEvent("
                  "new KeyboardEvent('keydown', {key:'Enter', bubbles:true, cancelable:true}))")
    wait(page, "recording.revision === 1")
    page.wait_ms(150)
    assert page.evaluate("calls.filter(call => call.method === 'POST').length") == 1
    page.evaluate("document.querySelector('#recording-test .external-video > button').click()")
    wait(page, "document.querySelector('#recording-test .external-video-actions').textContent.includes('Download to enable full replay features.')")
    assert page.evaluate("calls.filter(call => call.method === 'POST').length") == 1
    click(page, "Download")
    wait(page, "calls.filter(call => call.method === 'POST').length === 2")
    assert page.evaluate("calls.filter(call => call.method === 'POST')[1].body.url") == 'https://recording.example/new'


@pytest.mark.parametrize("frame_step", [None, 1 / 60])
def test_cached_playback_probes_timing_only_after_download_click(page, frame_step):
    # A MediaSource with no appended data keeps the video loading: this tests
    # capability/selection, without conflating it with codec/seek accuracy.
    page.evaluate(f"window.mediaReply = {{state:'ready', start_s:12, "
                  f"frame_step_s:{json.dumps(frame_step)}, "
                  "clip_url:URL.createObjectURL(new MediaSource())}")
    mount(page, url='https://recording.example/cached', library=True)
    wait(page, "calls.some(call => call.path === '/api/media')")
    assert page.evaluate("calls.filter(call => call.method === 'POST').length") == 0
    page.evaluate("document.querySelector('#recording-test .external-video > button').click()")
    wait(page, "document.querySelector('#recording-test .external-video-actions').textContent.includes('Download to enable full replay features.')")
    assert page.evaluate("calls.filter(call => call.method === 'POST').length") == 0
    assert page.evaluate("document.querySelector('#recording-test .external-video-local') === null")
    click(page, "Download")
    page.wait_for("#recording-test .external-video-local video")
    wait(page, "calls.filter(call => call.method === 'POST').length === 1")
    assert page.evaluate("document.querySelector('#recording-test video').src.startsWith('blob:')")
    assert page.evaluate("[...document.querySelectorAll('#recording-test .replay-transport button')].map(b => b.textContent.trim())") == ['Start', 'Back 1', 'Play', 'Forward 1']
    assert page.evaluate("document.querySelector('#recording-test .replay-transport button:nth-child(2)').disabled") == (frame_step is None)
    assert page.evaluate("document.querySelector('#recording-test .replay-transport button:nth-child(4)').disabled") == (frame_step is None)
    # The shared transport now retains shortcut help for every recording and
    # explains unavailable frame timing beside the disabled step controls.
    note = page.evaluate("document.querySelector('#recording-test .replay-frame-note').textContent")
    assert ('Frame timing unavailable' in note) == (frame_step is None), note
    assert ('← → Step' in note) == (frame_step is not None), note
    assert page.evaluate("document.querySelectorAll('#recording-test iframe').length") == 0


def test_empty_link_status_reserves_no_blank_space(page):
    mount(page)
    assert page.evaluate("document.querySelector('#recording-test .recording-link-status').getBoundingClientRect().height") == 0


def test_compare_intent_loads_cached_recording_without_native_extraction(page):
    page.evaluate("""(async () => {
      const {h, render} = await import('preact');
      const {Compare} = await import('/ui/components/compare.js');
      window.cachedClip = URL.createObjectURL(new MediaSource());
      render(h(Compare, {t:{view:{}}, active:true, clearIntent:() => {},
        intent:{attemptId:4242, entity:'star:2:4', strat:null,
          recording:{clip_url:cachedClip, start_s:12}}}),
        document.querySelector('#recording-test'));
    })()""")
    page.wait_for('#recording-test .compare-col video')
    assert page.evaluate("document.querySelector('#recording-test .compare-col video').src === cachedClip")
    assert page.evaluate("calls.filter(call => call.path.endsWith('/replay')).length") == 0


def test_export_html_preserves_exact_safe_link_and_paint(page):
    copied = page.evaluate("""(async () => {
      const {columnHtml} = await import('/ui/components/scorecard.js');
      return columnHtml([
        {text:'11.33',platform:'n64',video:'https://example.com/watch?t=4&name="clip"'},
        {text:'12.00',platform:'emu',video:'javascript:alert(1)'},
        {text:'',platform:null},
        {text:'1:21.93',platform:'emu',video:'https://example.com/long'}],
        {n64_fill:'#123456',emu_fill:'#654321',font_color:'#FFFFFF',font_family:'Roboto Mono'});
    })()""")
    from html.parser import HTMLParser

    class ReadCells(HTMLParser):
        def __init__(self):
            super().__init__()
            self.links = []
            self.cells = []
            self.values = []

        def handle_starttag(self, tag, attrs):
            if tag == 'a':
                self.links.append(dict(attrs)['href'])
            if tag == 'td':
                self.cells.append(dict(attrs))

        def handle_data(self, value):
            self.values.append(value)

    reader = ReadCells()
    reader.feed(copied)
    # Measured in an @-formatted Ultimate Sheet column: explicit metadata
    # survives; visible formula text gets apostrophe-escaped at paste time.
    assert copied.startswith('<google-sheets-html-origin>')
    assert reader.links == ['https://example.com/watch?t=4&name="clip"', 'https://example.com/long']
    assert reader.values == ['11.33', '12.00', '1:21.93']
    assert len(reader.cells) == 4
    assert json.loads(reader.cells[0]['data-sheets-value']) == {'1': 3, '3': 11.33}
    assert reader.cells[0]['data-sheets-formula'] == '=HYPERLINK("https://example.com/watch?t=4&name=""clip""",11.33)'
    assert 'data-sheets-formula' not in reader.cells[1]
    assert json.loads(reader.cells[3]['data-sheets-value']) == {'1': 2, '2': '1:21.93'}
    assert reader.cells[3]['data-sheets-formula'] == '=HYPERLINK("https://example.com/long","1:21.93")'
    assert 'background-color:#123456' in reader.cells[0]['style']
    assert reader.cells[2] == {}
