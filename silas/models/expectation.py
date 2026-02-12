from __future__ import annotations

from pydantic import BaseModel, model_validator


class Expectation(BaseModel):
    exit_code: int | None = None
    equals: str | None = None
    contains: str | None = None
    regex: str | None = None
    output_lt: float | None = None
    output_gt: float | None = None
    file_exists: str | None = None
    not_empty: bool | None = None

    @model_validator(mode="after")
    def _validate_exactly_one_predicate(self) -> Expectation:
        checks = [
            self.exit_code is not None,
            self.equals is not None,
            self.contains is not None,
            self.regex is not None,
            self.output_lt is not None,
            self.output_gt is not None,
            self.file_exists is not None,
            self.not_empty is True,
        ]
        selected = sum(checks)
        if selected != 1:
            raise ValueError("Expectation must define exactly one predicate")
        return self


__all__ = ["Expectation"]
