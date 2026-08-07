"""Error types and the issue log used for validation reporting."""

from __future__ import annotations

from dataclasses import dataclass, field


class LinerNotesError(Exception):
    """Base class for all errors raised by this package."""


class ValidationError(LinerNotesError):
    """The album file is structurally unusable."""


class LayoutError(LinerNotesError):
    """Content cannot be placed even under the most permissive settings."""


@dataclass(frozen=True)
class Issue:
    level: str  # "error" | "warning" | "info"
    code: str
    message: str
    where: str = ""

    def format(self) -> str:
        prefix = {"error": "ERROR", "warning": "WARN ", "info": "INFO "}[self.level]
        location = f" [{self.where}]" if self.where else ""
        return f"{prefix} {self.code}{location}: {self.message}"


@dataclass
class IssueLog:
    """Collects validation and layout findings so the CLI can report them all
    at once rather than failing on the first problem."""

    issues: list[Issue] = field(default_factory=list)

    def error(self, code: str, message: str, where: str = "") -> None:
        self.issues.append(Issue("error", code, message, where))

    def warn(self, code: str, message: str, where: str = "") -> None:
        self.issues.append(Issue("warning", code, message, where))

    def info(self, code: str, message: str, where: str = "") -> None:
        self.issues.append(Issue("info", code, message, where))

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.level == "error"]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.level == "warning"]

    def raise_if_errors(self) -> None:
        if self.errors:
            detail = "\n".join(i.format() for i in self.errors)
            raise ValidationError(f"album file has {len(self.errors)} error(s):\n{detail}")
