"""Bootstrap the process-local capability for deliberate native HTTP actions."""
import json
import urllib.error
import urllib.request
from urllib.parse import urlsplit


def mutation_headers(base: str, *, timeout=1.0):
    parsed = urlsplit(base)
    if (parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path not in {"", "/"}):
        raise ValueError("local client requires a loopback app address")
    try:
        with urllib.request.urlopen(base.rstrip("/") + "/api/local-access",
                                    timeout=timeout) as response:
            payload = json.loads(response.read(1024))
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return {}  # A previously shipped app predates the local capability.
        raise
    token = payload.get("request_token")
    if not isinstance(token, str) or not 32 <= len(token) <= 128:
        raise ValueError("local app returned an invalid request capability")
    return {"X-SM64-Request-Token": token}
