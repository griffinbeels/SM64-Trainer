"""Protect the loopback API against browser cross-origin requests and rebinding.

This is a local request capability, not a cloud credential. It rotates on every
server start and is obtained only from the exact loopback origin. HTTP clients
must bootstrap it before mutations. OAuth returns are GETs whose one-use PKCE
state is checked by the account service; they never bypass these host checks.
"""
import secrets
import logging

from starlette.datastructures import Headers
from starlette.responses import JSONResponse

HEADER = "X-SM64-Request-Token"
BOOTSTRAP = "/api/local-access"
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
CALLBACK_PATH = '/api/community/auth/callback'


class OAuthAccessFilter(logging.Filter):
    """Uvicorn's access tuple must never persist an OAuth return query."""
    def filter(self, record):
        if isinstance(record.args, tuple) and len(record.args) == 5:
            client, method, path, protocol, status = record.args
            if isinstance(path, str) and path.split('?', 1)[0] == CALLBACK_PATH:
                record.args = (client, method, CALLBACK_PATH, protocol, status)
        return True


class LocalAccess:
    def __init__(self, port: int):
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError("invalid local API port")
        self.hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        self.origins = {f"http://{host}" for host in self.hosts}
        self.token = secrets.token_urlsafe(32)

    def permitted(self, headers, *, websocket=False, oauth_return=False):
        if headers.get("host", "").lower() not in self.hosts:
            return False
        origin = headers.get("origin")
        if oauth_return and origin is None:
            return True  # Exact Host already checked; one-use state protects this navigation.
        if origin is not None and origin not in self.origins:
            return False
        if headers.get("sec-fetch-site") in {"cross-site", "same-site"}:
            return False
        # Native loopback clients may have no Origin. Browser sockets always
        # have one, so reject an omitted Origin for that entry point.
        return not websocket or origin in self.origins


class LocalAccessMiddleware:
    def __init__(self, app, *, access: LocalAccess):
        self.app, self.access = app, access

    async def __call__(self, scope, receive, send):
        if scope["type"] not in {"http", "websocket"}:
            return await self.app(scope, receive, send)
        headers = Headers(scope=scope)
        websocket = scope["type"] == "websocket"
        oauth_return = (not websocket and scope['method'] == 'GET' and scope['path'] == CALLBACK_PATH
                        and headers.get('sec-fetch-mode') == 'navigate'
                        and headers.get('sec-fetch-dest') == 'document')
        if not self.access.permitted(headers, websocket=websocket, oauth_return=oauth_return):
            return await self._deny(scope, receive, send, websocket)
        if websocket:
            return await self.app(scope, receive, send)
        if scope["method"] not in _SAFE_METHODS:
            provided = headers.get(HEADER, "")
            if not secrets.compare_digest(provided, self.access.token):
                return await self._deny(scope, receive, send, False)
        await self.app(scope, receive, send)

    async def _deny(self, scope, receive, send, websocket):
        if websocket:
            await send({"type": "websocket.close", "code": 1008})
            return
        response = JSONResponse({"detail": "Local request access denied."}, 403,
                                headers={"Cache-Control": "no-store"})
        await response(scope, receive, send)


def mount_local_access(app, *, port, protected=True):
    access_logger = logging.getLogger('uvicorn.access')
    if not any(isinstance(item, OAuthAccessFilter) for item in access_logger.filters):
        access_logger.addFilter(OAuthAccessFilter())
    access = LocalAccess(port)
    app.state.local_access = access
    if protected:
        app.add_middleware(LocalAccessMiddleware, access=access)

    @app.get(BOOTSTRAP, include_in_schema=False)
    def bootstrap():
        return JSONResponse({"request_token": access.token}, headers={
            "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})

    return access
