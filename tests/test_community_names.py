"""Normalized account names remain bounded at both native and API boundaries."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from sm64_events.community.names import checked_name
from sm64_events.server.community_api import Profile, Rename, create_community_router


@pytest.mark.parametrize("name", ["SullyLikesBigChungus", "a" * 20, "ガ" * 20, "😀" * 15 + "abc", "abcd"])
def test_supported_names_fit_both_limits(name):
    assert checked_name(name) == name
    assert Rename(name=name).name == name


# "abc" and " ab " sit below Speedrun.com's four-character minimum (round 41).
@pytest.mark.parametrize("name", ["a" * 21, "😀" * 15 + "abcd", "ﬃ" * 7, " ", "abc", " ab ", "name\u200b", "line\u2028break", "\ud800"])
def test_names_cannot_bypass_limits_or_insert_controls(name):
    with pytest.raises(ValueError):
        checked_name(name)
    with pytest.raises(ValidationError):
        Rename(name=name)
    with pytest.raises(ValidationError):
        Profile(name=name, terms_version="t1", privacy_version="p1")


def test_normalization_precedes_limits_and_keeps_sharing_explicit():
    assert Rename(name=" ＦｏｒｍＦｒａｍｅ ").name == "FormFrame"
    assert Rename(name="ﬃ" * 6).name == "ffi" * 6
    assert Profile(name="Runner", terms_version="t1", privacy_version="p1").sharing is False


def test_invalid_local_rename_never_calls_remote_account():
    class Account:
        calls = []

        def snapshot(self):
            return {"enabled": True}

        async def rename(self, name):
            self.calls.append(name)

    account = Account()
    app = FastAPI()
    app.include_router(create_community_router(account))
    with TestClient(app) as client:
        assert client.put("/api/community/account/name", json={"name": "a" * 21}).status_code == 422
        assert account.calls == []
        assert client.put("/api/community/account/name", json={"name": " Ｒｕｎｎｅｒ "}).status_code == 200
        assert account.calls == ["Runner"]
