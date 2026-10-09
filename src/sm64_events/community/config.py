"""Public build configuration; credentials never belong in packaged settings."""
from dataclasses import dataclass
from urllib.parse import urlsplit

CALLBACK = "http://127.0.0.1:8072/api/community/auth/callback"


def checked_url(value, *, local=False):
    if not isinstance(value, str) or len(value) > 2048:
        raise ValueError("Invalid public community URL.")
    parsed = urlsplit(value)
    if (not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.scheme not in ("https", "http")
            or parsed.scheme == "http" and not (local and parsed.hostname in ("127.0.0.1", "localhost", "::1"))):
        raise ValueError("Community endpoints require HTTPS.")
    return value.rstrip("/")


@dataclass(frozen=True)
class CommunityConfig:
    public_url: str = ""
    supabase_url: str = ""
    publishable_key: str = ""
    oauth_client_id: str = ""
    callback_url: str = CALLBACK
    channel: str = "beta"
    enabled: bool = False
    allow_local_http: bool = False
    portal_url: str | None = None

    def __post_init__(self):
        if type(self.enabled) is not bool or type(self.allow_local_http) is not bool:
            raise ValueError("Community switches must be boolean.")
        if not isinstance(self.channel, str) or not 1 <= len(self.channel) <= 64:
            raise ValueError("Invalid community build channel.")
        if self.callback_url != CALLBACK:
            raise ValueError("The beta callback must match its registered loopback URI.")
        if self.enabled:
            object.__setattr__(self, "public_url", checked_url(self.public_url, local=self.allow_local_http))
            object.__setattr__(self, "supabase_url", checked_url(self.supabase_url, local=self.allow_local_http))
            if not self.oauth_client_id or not self.publishable_key:
                raise ValueError("Community sign-in needs public provider configuration.")
            object.__setattr__(self, "portal_url", checked_url(self.portal_url or self.public_url + "/auth/start", local=self.allow_local_http))


class CommunityError(ValueError):
    """Safe local explanation: never include remote bodies, tokens or URLs."""
    def __init__(self, code, message="Community access is temporarily unavailable.", *, status=None):
        super().__init__(message)
        self.code, self.status = code, status
        self.public_message = message
