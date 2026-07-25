"""Typed domain errors and their HTTP mapping.

The HTTP layer is thin: it translates these into the ``ErrorResponse`` shape and never
invents its own status codes.
"""

from __future__ import annotations


class MemoryCoreError(Exception):
    """Base class.  ``code`` is the stable machine-readable identifier for clients."""

    code = "internal_error"
    status = 500

    def __init__(self, message: str, **details: object) -> None:
        super().__init__(message)
        self.message = message
        self.details: dict[str, object] = details


class ChallengeNotFound(MemoryCoreError):
    code = "challenge_not_found"
    status = 404


class ChallengeExists(MemoryCoreError):
    code = "challenge_exists"
    status = 409


class RevisionConflict(MemoryCoreError):
    """The caller's ``expected_revision`` no longer matches the stored state.

    The client must re-read and retry.  This — not atomic rename — is what actually
    prevents logical lost updates.
    """

    code = "revision_conflict"
    status = 409


class InvalidRequest(MemoryCoreError):
    code = "invalid_request"
    status = 400


class TemplateNotFound(MemoryCoreError):
    code = "template_not_found"
    status = 404


class InvalidTemplate(MemoryCoreError):
    code = "invalid_template"
    status = 500


class ProposalInvalid(MemoryCoreError):
    """The Summarizer returned something that failed schema or epistemic validation."""

    code = "proposal_invalid"
    status = 422


class SummarizerUnavailable(MemoryCoreError):
    """The Summarizer backend failed to answer: down, unreachable, or too slow.

    Retryable.  The input is already in the journal, so retrying the ingest is safe.
    """

    code = "summarizer_unavailable"
    status = 502


class SummarizerUnauthorized(MemoryCoreError):
    """The Summarizer backend rejected *our* credentials.

    Deliberately **not** a 502 and deliberately not a 401.  502 would tell the client to
    retry, and this never succeeds on retry.  401 would suggest the caller needs to
    authenticate to Memory Core, when in fact the operator of this service has to fix an
    environment variable.  500 says the right thing: the server is misconfigured, a human
    must intervene, do not retry.
    """

    code = "summarizer_unauthorized"
    status = 500


class StateCorrupt(MemoryCoreError):
    code = "state_corrupt"
    status = 500
