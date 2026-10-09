"""Public packaged build identity, independent of environment and release feeds.

Only a frozen application's fixed bundle file selects beta behavior. An absent
file is the historical stable build; a present invalid file fails closed before
paths, logging, desktop imports or updater work can use stable defaults.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from functools import lru_cache
import json
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit
from uuid import UUID

CONFIG_FILENAME = "build-channel.json"
CONFIG_SCHEMA = 1
STABLE_APP_DIR = "SM64Trainer"
BETA_APP_DIR = "SM64TrainerBeta"
STABLE_PORT = 8064
SOURCE_PORT = 8065
BETA_PORT = 8072
_PUBLIC_FIELDS = {"community_url", "supabase_url", "supabase_publishable_key", "oauth_client_id"}
_FIELDS = {"schema", "channel", "build_id", *_PUBLIC_FIELDS}


@dataclass(frozen=True)
class BuildChannel:
    channel: str = "stable"
    build_id: str = ""
    community_url: str = ""
    supabase_url: str = ""
    supabase_publishable_key: str = ""
    oauth_client_id: str = ""

    @property
    def app_dir_name(self) -> str:
        return BETA_APP_DIR if self.channel == "beta" else STABLE_APP_DIR

    @property
    def display_name(self) -> str:
        return "SM64 Trainer Beta" if self.channel == "beta" else "SM64 Trainer"

    @property
    def default_port(self) -> int:
        return BETA_PORT if self.channel == "beta" else STABLE_PORT

    @property
    def updates_enabled(self) -> bool:
        return self.channel == "stable"

    def metadata(self) -> dict:
        """The complete public build file; no arbitrary extras are propagated."""
        return {"schema": CONFIG_SCHEMA, **asdict(self)}


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
        raise ValueError(f"beta {field} must be an HTTPS origin without credentials")


def parse_metadata(value: object) -> BuildChannel:
    """Validate a build file without echoing submitted values or secrets."""
    if not isinstance(value, dict) or set(value) - _FIELDS:
        raise ValueError("build metadata contains unsupported fields")
    if type(value.get("schema")) is not int or value["schema"] != CONFIG_SCHEMA:
        raise ValueError("unsupported build metadata schema")
    if value.get("channel") not in ("stable", "beta"):
        raise ValueError("unsupported build channel")
    fields = {name: value.get(name, "") for name in _FIELDS - {"schema"}}
    if any(not isinstance(item, str) or len(item) > 2048 for item in fields.values()):
        raise ValueError("build metadata fields must be bounded strings")
    build = BuildChannel(**fields)
    if build.channel == "stable":
        if any(fields[name] for name in _PUBLIC_FIELDS):
            raise ValueError("stable build cannot carry private-beta service configuration")
        return build
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", build.build_id):
        raise ValueError("beta build_id must be a short filename-safe identifier")
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
    if Path(sys.executable).name.lower() == f"{BETA_APP_DIR.lower()}.exe" and build.channel != "beta":
        raise ValueError("beta executable requires valid packaged beta metadata")
    return build
