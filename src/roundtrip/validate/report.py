"""Validation report model and its JSON / Markdown renderings."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

Severity = Literal["hard", "soft"]
Status = Literal["pass", "warn", "fail"]


class CheckResult(BaseModel):
    name: str
    severity: Severity
    passed: bool
    metric: float | None = None
    threshold: float | None = None
    detail: str = ""

    @property
    def outcome(self) -> str:
        if self.passed:
            return "pass"
        return "FAIL" if self.severity == "hard" else "warn"


class ValidationReport(BaseModel):
    month: str
    generated_at: dt.datetime
    schema_version: str
    row_count: int
    null_rates: dict[str, float] = Field(default_factory=dict)
    checks: list[CheckResult] = Field(default_factory=list)

    @property
    def status(self) -> Status:
        if any(c.severity == "hard" and not c.passed for c in self.checks):
            return "fail"
        if any(not c.passed for c in self.checks):
            return "warn"
        return "pass"

    @property
    def failed(self) -> list[CheckResult]:
        return [c for c in self.checks if not c.passed]

    def write(self, json_path: Path, md_path: Path) -> None:
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(self.model_dump_json(indent=2) + "\n", encoding="utf-8")
        md_path.parent.mkdir(parents=True, exist_ok=True)
        md_path.write_text(self.to_markdown(), encoding="utf-8")

    def to_markdown(self) -> str:
        lines = [
            f"# Validation report: {self.month}",
            "",
            f"- Status: **{self.status.upper()}**",
            f"- Generated: {self.generated_at.isoformat(timespec='seconds')}",
            f"- Schema version: `{self.schema_version}`",
            f"- Rows: {self.row_count:,}",
            "",
            "| Check | Severity | Outcome | Metric | Threshold | Detail |",
            "|---|---|---|---|---|---|",
        ]
        for c in self.checks:
            metric = "" if c.metric is None else _fmt(c.metric)
            thr = "" if c.threshold is None else _fmt(c.threshold)
            lines.append(
                f"| `{c.name}` | {c.severity} | {c.outcome} | {metric} | {thr} | {c.detail} |",
            )
        if self.null_rates:
            lines += ["", "## Null rates", "", "| Column | Null rate |", "|---|---|"]
            lines += [f"| `{k}` | {v:.4%} |" for k, v in self.null_rates.items()]
        return "\n".join(lines) + "\n"


def _fmt(x: float) -> str:
    if float(x).is_integer() and abs(x) >= 1000:
        return f"{int(x):,}"
    if abs(x) < 1:
        return f"{x:.4f}"
    return f"{x:,.2f}"


class ValidationError(RuntimeError):
    """Raised when a month fails a hard check. Carries the report for the caller."""

    def __init__(self, report: ValidationReport) -> None:
        names = ", ".join(c.name for c in report.failed if c.severity == "hard")
        super().__init__(f"validation failed for {report.month}: {names}")
        self.report = report
