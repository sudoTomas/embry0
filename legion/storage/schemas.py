"""Data model schemas for storage layer."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum


class JobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    AWAITING_INPUT = "awaiting_input"


@dataclass
class FailedTestCase:
    """One failing test case."""

    test_name: str
    file_path: str
    error_message: str
    line_number: int | None = None
    stdout_snippet: str = ""


@dataclass
class Finding:
    """Single validation finding with confidence."""

    type: str  # "test_failure", "type_error", "lint_error", "convention"
    confidence: int  # 0-100
    detail: str


@dataclass
class ValidationResult:
    """Structured validator output."""

    passed: bool
    category: str  # "full_pass" | "partial_pass" | "full_fail"
    tests_passed: bool
    summary: str = ""
    tests_failures: list[FailedTestCase] = field(default_factory=list)
    types_passed: bool = True
    type_errors: list[str] = field(default_factory=list)
    lint_passed: bool = True
    lint_errors: list[str] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)

    def to_retry_prompt(self) -> str:
        """Generate structured feedback for developer agent retry."""
        sections: list[str] = []
        if not self.tests_passed and self.tests_failures:
            failures = "\n".join(
                f"  - {f.test_name} ({f.file_path}:{f.line_number}): {f.error_message}"
                for f in self.tests_failures
            )
            sections.append(f"## Test Failures\n{failures}")
        if not self.types_passed and self.type_errors:
            errors = "\n".join(f"  - {e}" for e in self.type_errors)
            sections.append(f"## Type Errors\n{errors}")
        if not self.lint_passed and self.lint_errors:
            errors = "\n".join(f"  - {e}" for e in self.lint_errors)
            sections.append(f"## Lint Errors\n{errors}")
        high_confidence = [f for f in self.findings if f.confidence >= 80]
        if high_confidence:
            lines = "\n".join(f"  - [{f.type}] (confidence {f.confidence}): {f.detail}" for f in high_confidence)
            sections.append(f"## High-Confidence Findings\n{lines}")
        header = f"## Summary\n{self.summary}\n\n## Category: {self.category}\n"
        return header + "\n\n".join(sections)


@dataclass
class TraceRecord:
    """One row in the traces table — one agent invocation."""

    trace_id: str
    job_id: str
    agent_type: str  # "developer" | "validator" | "reviewer"
    model: str
    result: str  # "pass" | "fail" | "partial" | "error" | "timeout" | "budget_exceeded"
    cost_usd: float = 0.0
    duration_ms: int = 0
    tools_called: dict[str, int] = field(default_factory=dict)
    result_summary: str = ""

    @staticmethod
    def now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()
