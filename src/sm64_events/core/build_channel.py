"""Public packaged build identity, independent of environment and release feeds.

Only a frozen application's fixed bundle file selects installation behavior. An absent
file is the historical stable build; a present invalid file fails closed before
paths, logging, desktop imports or updater work can use stable defaults.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from functools import lru_cache
import json
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit
from uuid import UUID

CONFIG_FILENAME = "build-channel.json"
CONFIG_SCHEMA = 2
LEGACY_SCHEMA = 1
STABLE_APP_DIR = "SM64Trainer"
BETA_APP_DIR = "SM64TrainerBeta"
STABLE_PORT = 8064
SOURCE_PORT = 8065
BETA_PORT = 8072
_PUBLIC_FIELDS = {"community_url", "supabase_url", "supabase_publishable_key", "oauth_client_id"}
_FIELDS = {"schema", "installation", "channel", "build_id", "friend_invitation", *_PUBLIC_FIELDS}


@dataclass(frozen=True)
class BuildChannel:
    channel: str = "stable"
    build_id: str = ""
    community_url: str = ""
    supabase_url: str = ""
    supabase_publishable_key: str = ""
    oauth_client_id: str = ""
    friend_invitation: str = field(default="", repr=False)
    # Existing callers/schema1 retain the old private-beta identity. Every new
    # unified payload explicitly declares normal, independently of release type.
    installation: str = "legacy"

    @property
    def legacy_beta(self) -> bool:
        return self.installation == "legacy" and self.channel == "beta"

    @property
    def app_dir_name(self) -> str:
        return BETA_APP_DIR if self.legacy_beta else STABLE_APP_DIR

    @property
    def display_name(self) -> str:
        return "SM64 Trainer Beta" if self.legacy_beta else "SM64 Trainer"

    @property
    def default_port(self) -> int:
        return BETA_PORT if self.legacy_beta else STABLE_PORT

    @property
    def callback_url(self) -> str:
        return f"http://127.0.0.1:{self.default_port}/api/community/auth/callback"

    @property
    def community_enabled(self) -> bool:
        return all(getattr(self, name) for name in _PUBLIC_FIELDS)

    @property
    def account_namespace(self) -> str:
        """Keep normal beta/stable refresh credentials in one secure namespace."""
        return "beta" if self.legacy_beta else "stable"

    @property
    def updates_enabled(self) -> bool:
        return not self.legacy_beta

    def metadata(self) -> dict:
        """Packaged settings, including an optional private invitation.

        This serialization is for the installer only. Diagnostics expose named
        public fields directly; they must never serialize this capability.
        """
        value = {"schema": CONFIG_SCHEMA, **asdict(self)}
        if self.installation == "legacy":
            value["schema"] = LEGACY_SCHEMA
            value.pop("installation")
        if not self.friend_invitation:
            value.pop("friend_invitation")
        return value


def _https_origin(value: str, field: str) -> None:
    try:
        parts = urlsplit(value)
        valid = (parts.scheme == "https" and parts.hostname
                 and parts.username is None and parts.password is None
                 and not parts.query and not parts.fragment
                 and parts.path in ("", "/") and parts.port in (None, 443))
    except ValueError:
        valid = False
    if not valid:
        raise ValueError(f"{field} must be an HTTPS origin without credentials")


def parse_metadata(value: object) -> BuildChannel:
    """Validate a build file without echoing submitted values or secrets."""
    if not isinstance(value, dict) or set(value) - _FIELDS:
        raise ValueError("build metadata contains unsupported fields")
    if type(value.get("schema")) is not int or value["schema"] not in (LEGACY_SCHEMA, CONFIG_SCHEMA):
        raise ValueError("unsupported build metadata schema")
    if value["schema"] == LEGACY_SCHEMA and "installation" in value:
        raise ValueError("legacy build metadata cannot change installation identity")
    if value["schema"] == CONFIG_SCHEMA and value.get("installation") != "normal":
        raise ValueError("unified build metadata requires the normal installation identity")
    if value.get("channel") not in ("stable", "beta"):
        raise ValueError("unsupported build channel")
    fields = {name: value.get(name, "") for name in _FIELDS - {"schema", "installation"}}
    if any(not isinstance(item, str) or len(item) > 2048 for item in fields.values()):
        raise ValueError("build metadata fields must be bounded strings")
    build = BuildChannel(**fields, installation="normal" if value["schema"] == CONFIG_SCHEMA else "legacy")
    if build.channel == "stable" and build.installation == "legacy":
        if any(fields[name] for name in _PUBLIC_FIELDS) or build.friend_invitation:
            raise ValueError("stable build cannot carry private-beta service configuration")
        return build
    if (build.channel == "beta" or build.build_id) and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", build.build_id):
        raise ValueError("build_id must be a short filename-safe identifier")
    if build.friend_invitation and not re.fullmatch(r"[A-Za-z0-9_-]{43}", build.friend_invitation):
        raise ValueError("friend invitation must be a bounded private capability")
    if build.friend_invitation and not all(fields[name] for name in _PUBLIC_FIELDS):
        raise ValueError("beta friend invitation requires complete community configuration")
    if not any(fields[name] for name in _PUBLIC_FIELDS):
        return build  # Offline builds keep their declared installation identity.
    if not all(fields[name] for name in _PUBLIC_FIELDS):
        raise ValueError('community requires all four public service fields')
    _https_origin(build.community_url, "community_url")
    _https_origin(build.supabase_url, "supabase_url")
    if not re.fullmatch(r"sb_publishable_[A-Za-z0-9_-]{1,256}", build.supabase_publishable_key):
        raise ValueError("beta requires a public Supabase publishable key")
    try:
        if str(UUID(build.oauth_client_id)) != build.oauth_client_id:
            raise ValueError
    except ValueError:
        raise ValueError("beta oauth_client_id must be the registered client UUID") from None
    return build


def read_metadata(path: Path) -> BuildChannel:
    try:
        if path.stat().st_size > 8192:
            raise ValueError("build metadata exceeds its size limit")
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ValueError("cannot read packaged build metadata") from None
    return parse_metadata(value)


@lru_cache(maxsize=1)
def _packaged_build(bundle: str) -> BuildChannel:
    path = Path(bundle) / CONFIG_FILENAME
    # Missing is the sole stable fallback; unreadable/malformed is an error.
    try:
        path.stat()
    except FileNotFoundError:
        return BuildChannel()
    except OSError:
        raise ValueError("cannot read packaged build metadata") from None
    return read_metadata(path)


def current_build() -> BuildChannel:
    if not bool(getattr(sys, "frozen", False)):
        return BuildChannel()
    bundle = str(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    build = _packaged_build(bundle)
    if Path(sys.executable).name.lower() == f"{BETA_APP_DIR.lower()}.exe" and not build.legacy_beta:
        raise ValueError("beta executable requires valid packaged beta metadata")
    return build
