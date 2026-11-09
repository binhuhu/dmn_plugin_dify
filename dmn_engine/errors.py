"""Error type shared by the engine. Every refusal carries a stable code."""

from __future__ import annotations


class DmnError(Exception):
    """A refusal with a stable machine code and, when known, a location.

    ``where`` is a JSON-pointer-like path into the normalized table, for example
    ``rules[3].when[1]`` or ``outputs[0].values``.
    """

    def __init__(self, code: str, message: str, where: str | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.where = where

    def as_dict(self) -> dict:
        out = {"code": self.code, "message": self.message}
        if self.where:
            out["where"] = self.where
        return out
