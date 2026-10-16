"""Headless, fully intercepted portal fixture; no provider/network or live service."""
import asyncio
import json
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse, parse_qs, urlencode
from playwright.async_api import async_playwright
from sm64_events.core.childproc import quiet_spawn_kwargs

ROOT = Path(__file__).resolve().parents[1]
ORIGIN = 'https://community.example.invalid'
PUBLIC_BETA = '--public-beta' in sys.argv
CONFIG = {'v': 1, 'terms_url': ORIGIN + '/policies#terms', 'auth': {
    'supabase_url': 'https://fixture.supabase.co', 'publishable_key': 'public-fixture-only',
    'client_id': 'client-fixture', 'native_callback': 'http://127.0.0.1:8072/api/community/auth/callback',
    'native_clients': [{'client_id': 'client-fixture', 'callback': 'http://127.0.0.1:8072/api/community/auth/callback'},
                       {'client_id': 'normal-fixture', 'callback': 'http://127.0.0.1:8064/api/community/auth/callback'}]}}

def production_portal():
    """The Worker's real portal and policy responses, fetched before the event loop starts."""
    result=subprocess.run(['C:/Program Files/nodejs/node.exe', 'test/portal-response.js', *(['--public-beta'] if PUBLIC_BETA else [])], cwd=ROOT,
        capture_output=True, text=True, encoding='utf-8', timeout=20, check=False,
        **quiet_spawn_kwargs())
    assert result.returncode==0, result.stderr[-1000:]
    return json.loads(result.stdout)

async def fixture_context(browser, actual_portal, seen):
    """A context whose requests the fixture answers and records in seen; anything unexpected fails."""
    context = await browser.new_context(viewport={'width': 400, 'height': 850})
    await context.add_init_script("""if(location.origin==='https://community.example.invalid'){
              sessionStorage.setItem('sm64-beta-oauth-attempt',JSON.stringify({provider:'email',state:'b'.repeat(32),expires:Date.now()+300000,authorizationID:'fixture-authorization'}));}
            """)
    await context.add_init_script("""if(location.origin==='https://community.example.invalid'){
              const save=Storage.prototype.setItem;Storage.prototype.setItem=function(key,value){
                if(key==='sm64-beta-oauth-attempt'){const a=JSON.parse(value);if(a.clientID)console.info('fixture-pair:'+JSON.stringify({clientID:a.clientID,callback:a.callback}));}
                return save.call(this,key,value);};}
            """)
    async def route(request_route):
        req = request_route.request
        seen.append(req.url)
        u = urlparse(req.url)
        if u.netloc == 'fixture.supabase.co':
            if u.path in ('/auth/v1/authorize', '/auth/v1/oauth/authorize'):
                return await request_route.fulfill(status=200, content_type='text/html', body='Provider fixture reached')
            raise AssertionError('Unexpected provider network: ' + u.path)
        if u.netloc != 'community.example.invalid':
            raise AssertionError('Unexpected outbound origin')
        if u.path == '/v1/config':
            return await request_route.fulfill(status=200, content_type='application/json', body=json.dumps(CONFIG))
        if u.path in ('/auth/authorize', '/auth/start'):
            return await request_route.fulfill(status=200, headers=actual_portal['headers'], body=actual_portal['html'])
        if u.path == '/policies':
            return await request_route.fulfill(status=200, headers=actual_portal['policy']['headers'], body=actual_portal['policy']['html'])
        paths = {'/auth/authorize': 'index.html', '/portal.js': 'portal.js', '/portal.css': 'portal.css',
                 '/google-g.png': 'google-g.png', '/discord-clyde-white.svg': 'discord-clyde-white.svg', '/google-sans.ttf': 'google-sans.ttf',
                 '/twitch-glitch-white.svg': 'twitch-glitch-white.svg'}
        path = ROOT / 'public' / paths[u.path]
        types = {'.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.png': 'image/png', '.svg': 'image/svg+xml', '.ttf': 'font/ttf'}
        await request_route.fulfill(status=200, content_type=types[path.suffix], body=path.read_bytes())
    await context.route('**/*', route)
    return context

async def check_login(page):
    await page.goto(ORIGIN + '/auth/authorize?authorization_id=fixture-authorization')
    await page.get_by_text('Choose how to sign in.', exact=True).wait_for()
    await page.evaluate('document.fonts.ready')
    for provider in ['Discord', 'Google', 'Twitch']:
        assert await page.get_by_role('button', name='Continue with ' + provider).is_visible()
    assert await page.locator('#email').is_visible()
    geometry = await page.evaluate("""() => {const boxes=Array.from(document.querySelectorAll('.provider')).map(b=>{const r=b.getBoundingClientRect();return {width:r.width,height:r.height}});return {boxes,overflow:document.documentElement.scrollWidth>innerWidth,font:document.fonts.check('500 14px GoogleSans'),images:Array.from(document.querySelectorAll('.provider img')).every(i=>i.complete&&i.naturalWidth>0)};}""")
    assert len(geometry['boxes']) == 3
    assert all(box == geometry['boxes'][0] and box['height'] >= 44 for box in geometry['boxes'])
    assert geometry['images']
    assert not geometry['overflow'] and geometry['font']
    output = ROOT / '.wrangler' / 'portal-smoke.png'
    output.parent.mkdir(exist_ok=True)
    await page.screenshot(path=str(output), full_page=True)

async def check_social_returns(page, seen):
    for provider in ['Google', 'Twitch']:
        if provider != 'Google':
            await page.goto(ORIGIN + '/auth/authorize?authorization_id=fixture-authorization')
            await page.get_by_text('Choose how to sign in.', exact=True).wait_for()
        seen.clear()
        await page.get_by_role('button', name='Continue with ' + provider).click()
        await page.get_by_text('Provider fixture reached', exact=True).wait_for()
        request = next(parse_qs(urlparse(url).query) for url in seen if urlparse(url).path == '/auth/v1/authorize')
        assert request['provider'] == [provider.lower()]
        if provider == 'Google':
            assert request['scopes'] == ['openid email profile']
        else:
            assert 'scopes' not in request
        assert request['redirect_to'] == [ORIGIN + '/auth/callback']

async def check_native_pairs(page, seen, saved_pairs):
    for pair in CONFIG['auth']['native_clients']:
        native = 'https://fixture.supabase.co/auth/v1/oauth/authorize?' + urlencode({
            'client_id': pair['client_id'], 'redirect_uri': pair['callback'], 'response_type': 'code',
            'code_challenge_method': 'S256', 'code_challenge': 'a' * 43, 'state': 'b' * 32,
            'scope': 'openid email profile'})
        seen.clear()
        await page.goto(ORIGIN + '/auth/start?' + urlencode({'provider': 'discord', 'native_authorize': native}))
        await page.get_by_text('Provider fixture reached', exact=True).wait_for()
        request = next(parse_qs(urlparse(url).query) for url in seen if urlparse(url).path == '/auth/v1/oauth/authorize')
        assert request['client_id'] == [pair['client_id']] and request['redirect_uri'] == [pair['callback']]
        assert saved_pairs[-1] == {'clientID': pair['client_id'], 'callback': pair['callback']}

async def check_policies(page):
    await page.goto(ORIGIN+'/policies')
    await page.get_by_role('heading', name='Community ghosts '+('open' if PUBLIC_BETA else 'private')+' beta', exact=True).wait_for()
    if PUBLIC_BETA:
        assert await page.get_by_text('Anyone can join the beta', exact=False).is_visible()
    assert 'Admitted signed-in players can receive' in await page.locator('main').inner_text()
    assert await page.get_by_role('heading', name='Terms', exact=True).is_visible()
    assert await page.get_by_role('heading', name='Privacy notice', exact=True).is_visible()
    assert 'Fixture operator' in await page.locator('main').inner_text()
    await page.screenshot(path=str(ROOT/'.wrangler'/('policy-public-smoke.png' if PUBLIC_BETA else 'policy-smoke.png')))

async def main(actual_portal):
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel='chrome', headless=True)
        try:
            seen, errors, saved_pairs = [], [], []
            context = await fixture_context(browser, actual_portal, seen)
            page = await context.new_page()
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.on('console', lambda msg: errors.append(msg.text) if msg.type == 'error' else None)
            page.on('console', lambda msg: saved_pairs.append(json.loads(msg.text.removeprefix('fixture-pair:')))
                    if msg.text.startswith('fixture-pair:') else None)
            await check_login(page)
            await check_social_returns(page, seen)
            await check_native_pairs(page, seen, saved_pairs)
            await check_policies(page)
            assert not errors, errors
            print('Portal/policies browser fixture with production response CSP: zero console/page errors; all intercepted requests 200; login/assets/font/provider geometry/Google and Twitch callbacks, exact normal/legacy native pair persistence and operator-configured Terms/Privacy reachable. Screenshots .wrangler/portal-smoke.png and policy-smoke.png')
            await context.close()
        finally:
            await browser.close()

asyncio.run(main(production_portal()))
