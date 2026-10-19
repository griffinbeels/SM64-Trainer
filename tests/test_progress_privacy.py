"""Lapsed owners can remove their own publication without granting new consent."""
import asyncio
from copy import deepcopy
from uuid import UUID

import pytest

from sm64_events.community import CommunityError
from sm64_events.community.progress_privacy import ProgressPrivacy
from test_progress_sync import Account, OTHER, OWNER, publication


class PrivacyAccount(Account):
    def __init__(self, status):
        super().__init__(publication())
        self.state["status"] = status
        self.changed = lambda: None
        self.denial = None
        self.effects = []

    async def request_json(self, method, path, **kwargs):
        self.calls.append((method, path, deepcopy(kwargs)))
        self.changed()
        if self.denial:
            raise self.denial
        if method == "PUT":
            if self.state["status"] == "deleted":
                raise CommunityError("deleted", "private-remote-description", status=403)
            body = kwargs["json"]
            assert body["expected_revision"] == self.control["revision"]
            self.effects.append(body["action"])
            self.control.update(enabled=False, revision=self.control["revision"] + 1)
            if body["action"] == "withdraw":
                self.control.update(visible=False, content_hash=None, captured_utc=None, synced_utc=None)
        return deepcopy(self.control)


@pytest.mark.parametrize("status", ["active", "suspended", "not_invited", "consent_required", "rename_required", "deleted", "signed_out"])
def test_owner_read_is_independent_of_active_publication_access_and_needs_no_database(status):
    async def scenario():
        account = PrivacyAccount(status)
        if status == "signed_out":
            account.state.update(authenticated=False, stored=True)
        privacy = ProgressPrivacy(account)
        value = await privacy.read()
        assert value["available"] and value["publication"]["uid"] == OWNER and value["status"] == status
        assert account.calls == [("GET", "/v2/account/progress", {})] and not account.effects
    asyncio.run(scenario())


@pytest.mark.parametrize("status", ["suspended", "not_invited", "consent_required", "rename_required", "signed_out"])
@pytest.mark.parametrize("action", ["disable", "withdraw"])
def test_lapsed_owner_control_sends_only_explicit_removal_with_null_consent_and_publisher(status, action):
    async def scenario():
        account = PrivacyAccount(status)
        account.state.update(authenticated=False, stored=True)
        value = await ProgressPrivacy(account).control(action, 1)
        assert value["available"] and value["publication"]["enabled"] is False
        assert account.effects == [action]
        method, path, kwargs = account.calls[0]
        assert method == "PUT" and path == "/v2/account/progress/control"
        assert kwargs["json"] == {"action": action, "expected_revision": 1, "publisher": None, "notice_version": None}
        assert str(UUID(kwargs["headers"]["Idempotency-Key"])) == kwargs["headers"]["Idempotency-Key"]
        assert len(account.calls) == 1
    asyncio.run(scenario())


@pytest.mark.parametrize("action,revision", [("enable", 1), ("switch", 1), ("upload", 1), ("edit", 1),
                                           ("withdraw", True), ("disable", -1), ("withdraw", 2 ** 53)])
def test_privacy_object_cannot_enable_upload_edit_or_accept_unsafe_revisions(action, revision):
    async def scenario():
        account = PrivacyAccount("suspended")
        with pytest.raises(CommunityError) as error:
            await ProgressPrivacy(account).control(action, revision)
        assert error.value.code == "invalid_progress" and not account.calls and not account.effects
    asyncio.run(scenario())


@pytest.mark.parametrize("change", ["account", "epoch", "signed_out", "account_object"])
@pytest.mark.parametrize("failed", [False, True])
def test_late_responses_and_errors_never_return_another_account_publication(change, failed):
    async def scenario():
        account = PrivacyAccount("not_invited")
        privacy = ProgressPrivacy(account)
        def changed():
            if change == "account": account.state["uid"] = OTHER
            elif change == "epoch": account._epoch += 1
            elif change == "signed_out": account.state.update(uid=None, authenticated=False, stored=False)
            else: privacy.account = PrivacyAccount("active")
        account.changed = changed
        if failed: account.denial = CommunityError("network_unavailable", "private-remote-token", status=503)
        with pytest.raises(CommunityError) as error: await privacy.read()
        assert error.value.code == "canceled" and "private-remote" not in str(error.value)
    asyncio.run(scenario())


def test_deleted_owner_cannot_mutate_and_server_denial_is_sanitized():
    async def scenario():
        account = PrivacyAccount("deleted")
        with pytest.raises(CommunityError) as error:
            await ProgressPrivacy(account).control("withdraw", 1)
        assert error.value.code == "deleted" and error.value.status == 403
        assert "private-remote" not in str(error.value) and not account.effects
    asyncio.run(scenario())


def test_wrong_owner_or_malformed_control_response_is_not_returned():
    async def scenario():
        account = PrivacyAccount("suspended")
        account.control["uid"] = OTHER
        with pytest.raises(CommunityError) as error: await ProgressPrivacy(account).read()
        assert error.value.code == "invalid_response"
    asyncio.run(scenario())


@pytest.mark.parametrize("change", ["disabled", "missing_uid", "no_credentials", "invalid_uid", "absent_account"])
def test_missing_owner_cannot_read_or_change_remote_publication(change):
    async def scenario():
        account = PrivacyAccount("signed_out")
        if change == "disabled": account.state["enabled"] = False
        elif change == "missing_uid": account.state["uid"] = None
        elif change == "invalid_uid": account.state["uid"] = "private-invalid-identity"
        elif change == "no_credentials": account.state.update(authenticated=False, stored=False)
        privacy = ProgressPrivacy(None if change == "absent_account" else account)
        value = await privacy.read()
        assert not value["available"] and value["publication"] is None
        with pytest.raises(CommunityError): await privacy.control("disable", 1)
        assert not account.calls and not account.effects
    asyncio.run(scenario())
