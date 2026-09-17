"""Retain the actual native owner until each release operation succeeds."""
import ctypes
import sys


class CleanupPending(RuntimeError):
    """A failed constructor hands its partially built owner to its caller."""

    def __init__(self, owner, error):
        self.owner = owner
        super().__init__(f"{type(owner).__name__} cleanup pending: {error}")


def close_after_error(owner):
    """Used inside a constructor's except; never discard an unclosed owner."""
    previous = sys.exception()
    try:
        owner.close()
    except Exception as exc:  # noqa: BLE001 - arbitrary owned cleanup must transfer its surviving owner.
        retained = retain(retain(owner, exc), previous)
        raise CleanupPending(retained, exc) from exc


def retain(owner, error):
    """Keep a nested partial owner ahead of the owner whose close exposed it."""
    if isinstance(error, CleanupPending) and error.owner is not owner:
        return PendingOwners(error.owner, owner)
    return owner


class PendingOwners:
    """Bounded, deduplicated disposal order for nested constructor failures."""

    def __init__(self, *owners):
        self.owners = []
        for owner in owners:
            candidates = owner.owners if isinstance(owner, PendingOwners) else [owner]
            for candidate in candidates:
                if all(candidate is not held for held in self.owners):
                    self.owners.append(candidate)

    def close(self):
        while self.owners:
            try:
                self.owners[0].close()
            except CleanupPending as exc:
                self.owners = PendingOwners(exc.owner, *self.owners).owners
                raise
            self.owners.pop(0)


class CleanupRetrier:
    """Retry a callback without abandoning a partial owner it may expose."""

    def __init__(self, callback):
        self.callback = callback
        self.pending = None

    def __call__(self):
        try:
            if self.pending is not None:
                self.pending.close()
                self.pending = None
            self.callback()
        except CleanupPending as exc:
            self.pending = (retain(self.pending, exc) if self.pending is not None
                            else exc.owner)
            raise


class ProcessHandle:
    """A temporary identity probe still has an owner if CloseHandle fails."""

    def __init__(self, kernel, handle):
        self._k, self.handle = kernel, handle

    def close(self):
        release(self, "handle", "CloseHandle")


def release(owner, attribute, operation):
    """Clear a handle/view only after Win32 reports successful release."""
    value = getattr(owner, attribute)
    if value:
        if not getattr(owner._k, operation)(value):
            raise OSError(ctypes.get_last_error(),
                          f"{operation} failed; {attribute} remains owned")
        setattr(owner, attribute, None)


def release_many(owner, attributes):
    """Attempt independent releases, retaining each failure for the next close."""
    errors = []
    for attribute, operation in attributes:
        try:
            release(owner, attribute, operation)
        except OSError as exc:
            errors.append(exc)
    if errors:
        raise errors[0]
