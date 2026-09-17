"""Publication outcomes the GPU media sink (replay/gpumediaworker.py) reports.

Admission preserves the mux byte stream, but is not a publication receipt. Only
the archive writer exposes completed fragments.
"""


class PublicationBusyError(RuntimeError):
    """The writer still owns its archive; scratch must remain protected."""


class PublicationError(RuntimeError):
    """Publication failed, but the writer has demonstrably finished."""


class PublicationWriteError(RuntimeError):
    """Mux output refused; the session must still prove writer completion."""

