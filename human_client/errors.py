from __future__ import annotations

from dataclasses import dataclass
from typing import Any

OK = 0
USAGE_ERROR = 2
CONNECTION_ERROR = 3
NOT_FOUND = 4
REVISION_CONFLICT = 5
VALIDATION_ERROR = 6
SERVER_ERROR = 7
CAPTURE_START_ERROR = 8


@dataclass
class CoreError(RuntimeError):
    status_code: int
    body: dict[str, Any] | str

    def __str__(self) -> str:
        return f"Memory Core returned HTTP {self.status_code}: {self.body}"

    @property
    def code(self) -> str | None:
        if isinstance(self.body, dict):
            error = self.body.get("error")
            if isinstance(error, dict):
                code = error.get("code")
                return code if isinstance(code, str) else None
        return None

    @property
    def message(self) -> str:
        if isinstance(self.body, dict):
            error = self.body.get("error")
            if isinstance(error, dict):
                message = error.get("message")
                if isinstance(message, str):
                    return message
        return str(self.body)

    @property
    def details(self) -> dict[str, Any]:
        if isinstance(self.body, dict):
            error = self.body.get("error")
            if isinstance(error, dict):
                details = error.get("details")
                if isinstance(details, dict):
                    return details
        return {}


class CoreConnectionError(RuntimeError):
    pass


def exit_code_for_error(error: CoreError | CoreConnectionError) -> int:
    if isinstance(error, CoreConnectionError):
        return CONNECTION_ERROR
    if error.status_code == 404:
        return NOT_FOUND
    if error.status_code == 409 or error.code == "revision_conflict":
        return REVISION_CONFLICT
    if error.status_code in {400, 422}:
        return VALIDATION_ERROR
    return SERVER_ERROR
