"""Owner-only removal controls stay reachable without active publication access.

This object cannot enable, upload or edit a profile. Server revision and account
fences remain authoritative; a local database is neither read nor required.
"""
from uuid import uuid4

from .config import CommunityError
from .progress_state import checked_control, checked_uuid

_STATUSES = frozenset({"active", "new", "rename_required", "consent_required", "suspended", "not_invited", "deleted", "signed_out"})
_ERRORS = frozenset({"deleted", "suspended", "not_invited", "consent_required", "unauthorized", "signed_out",
                     "not_found", "revision_conflict", "network_unavailable", "rate_limited", "budget_exhausted",
                     "request_failed", "invalid_response", "response_too_large", "invalid_progress", "canceled"})


class ProgressPrivacy:
    def __init__(self, account):
        self.account = account

    def _snapshot(self):
        value = self.account.snapshot() if self.account is not None else {}
        return value if isinstance(value, dict) else {}

    def _status(self):
        status = self._snapshot().get("status")
        return status if isinstance(status, str) and status in _STATUSES else "unavailable"

    def _identity(self):
        value = self._snapshot()
        if value.get("enabled") is not True or not (value.get("authenticated") is True or value.get("stored") is True):
            raise CommunityError("sign_in_required", "Sign in to review or remove your published PBs.")
        try:
            uid = checked_uuid(value.get("uid"))
        except CommunityError:
            raise CommunityError("sign_in_required", "Sign in to review or remove your published PBs.") from None
        return self.account, uid, getattr(self.account, "_epoch", None)

    def _guard(self, identity):
        try:
            current = self._identity()
        except CommunityError:
            current = None
        if current is None or current[0] is not identity[0] or current[1:] != identity[1:]:
            raise CommunityError("canceled", "Your signed-in account changed. Open its privacy controls again.")

    async def _request(self, method, *, body=None):
        identity = self._identity()
        path = "/v2/account/progress" + ("/control" if method == "PUT" else "")
        kwargs = {} if body is None else {"json": body, "headers": {"Idempotency-Key": str(uuid4())}}
        try:
            value = await self.account.request_json(method, path, **kwargs)
        except CommunityError as error:
            self._guard(identity)
            code = error.code if error.code in _ERRORS else "request_failed"
            message = ("Publication changed. Refresh its privacy settings before trying again."
                       if code == "revision_conflict" else "Privacy controls are temporarily unavailable. Your local PBs remain saved.")
            raise CommunityError(code, message, status=error.status, retry_after=error.retry_after) from None
        self._guard(identity)
        return {"available": True, "reason": None, "publication": checked_control(value, identity[1]), "status": self._status()}

    async def read(self):
        try:
            self._identity()
        except CommunityError as error:
            return {"available": False, "reason": error.public_message, "publication": None, "status": self._status()}
        return await self._request("GET")

    async def control(self, action, expected_revision):
        if action not in ("disable", "withdraw") or type(expected_revision) is not int or not 0 <= expected_revision <= 9007199254740991:
            raise CommunityError("invalid_progress", "Choose pause or remove from the current privacy settings.")
        return await self._request("PUT", body={"action": action, "expected_revision": expected_revision,
                                                "publisher": None, "notice_version": None})
