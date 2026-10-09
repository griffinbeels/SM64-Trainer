"""Headless, fully intercepted portal fixture; no provider/network or live service."""
import asyncio
import json
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
ORIGIN = 'https://community.example.invalid'
CONFIG = {'v': 1, 'terms_url': ORIGIN + '/policies#terms', 'auth': {
    'supabase_url': 'https://fixture.supabase.co', 'publishable_key': 'public-fixture-only',
    'client_id': 'client-fixture', 'native_callback': 'http://127.0.0.1:8072/api/community/auth/callback'}}

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel='chrome', headless=True)
        try:
            context = await browser.new_context(viewport={'width': 400, 'height': 850})
            await context.add_init_script("""if(location.origin==='https://community.example.invalid'){
              sessionStorage.setItem('sm64-beta-oauth-attempt',JSON.stringify({provider:'email',state:'b'.repeat(32),expires:Date.now()+300000,authorizationID:'fixture-authorization'}));}
            """)
            seen, errors = [], []
            async def route(request_route):
                req = request_route.request
                seen.append(req.url)
                u = urlparse(req.url)
                if u.netloc == 'fixture.supabase.co':
                    if u.path == '/auth/v1/authorize':
                        return await request_route.fulfill(status=200, content_type='text/html', body='Provider fixture reached')
                    raise AssertionError('Unexpected provider network: ' + u.path)
                if u.netloc != 'community.example.invalid':
                    raise AssertionError('Unexpected outbound origin')
                if u.path == '/v1/config':
                    return await request_route.fulfill(status=200, content_type='application/json', body=json.dumps(CONFIG))
                paths = {'/auth/authorize': 'index.html', '/portal.js': 'portal.js', '/portal.css': 'portal.css',
                         '/google-g.png': 'google-g.png', '/discord-clyde-white.svg': 'discord-clyde-white.svg', '/google-sans.ttf': 'google-sans.ttf'}
                path = ROOT / 'public' / paths[u.path]
                types = {'.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.png': 'image/png', '.svg': 'image/svg+xml', '.ttf': 'font/ttf'}
                await request_route.fulfill(status=200, content_type=types[path.suffix], body=path.read_bytes())
            await context.route('**/*', route)
            page = await context.new_page()
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.on('console', lambda msg: errors.append(msg.text) if msg.type == 'error' else None)
            await page.goto(ORIGIN + '/auth/authorize?authorization_id=fixture-authorization')
            await page.get_by_text('Choose how to sign in.', exact=True).wait_for()
            await page.evaluate('document.fonts.ready')
            for provider in ['Discord', 'Google']:
                assert await page.get_by_role('button', name='Continue with ' + provider).is_visible()
            assert await page.locator('#email').is_visible()
            geometry = await page.evaluate("""() => {const d=document.querySelector('#discord').getBoundingClientRect(),g=document.querySelector('#google').getBoundingClientRect();return {dw:d.width,gw:g.width,dh:d.height,gh:g.height,overflow:document.documentElement.scrollWidth>innerWidth,font:document.fonts.check('500 14px GoogleSans')};}""")
            assert geometry['dw'] == geometry['gw'] and geometry['dh'] == geometry['gh'] and geometry['dh'] >= 44
            assert not geometry['overflow'] and geometry['font']
            output = ROOT / '.wrangler' / 'portal-smoke.png'
            output.parent.mkdir(exist_ok=True)
            await page.screenshot(path=str(output), full_page=True)
            await page.get_by_role('button', name='Continue with Google').click()
            await page.get_by_text('Provider fixture reached', exact=True).wait_for()
            provider = next(parse_qs(urlparse(url).query) for url in seen if urlparse(url).path == '/auth/v1/authorize')
            assert provider['provider'] == ['google']
            assert provider['scopes'] == ['openid email profile']
            assert provider['redirect_to'] == [ORIGIN + '/auth/callback']
            assert not errors, errors
            print('Portal browser fixture: zero console/page errors; all intercepted requests 200; email login visible; official local assets/font; equal provider geometry; Google dispatch and callback origin checked. Screenshot .wrangler/portal-smoke.png')
            await context.close()
        finally:
            await browser.close()

asyncio.run(main())
