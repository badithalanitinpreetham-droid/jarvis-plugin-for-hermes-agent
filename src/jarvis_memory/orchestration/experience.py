"""Turn workflow history into reusable, bounded operational experience."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List


@dataclass(frozen=True)
class ExperienceSummary:
    successful_tools: List[str] = field(default_factory=list)
    failed_tools: List[str] = field(default_factory=list)
    lessons: List[str] = field(default_factory=list)
    confidence: float = 0.0

    def as_dict(self) -> Dict[str, Any]:
        return {
            "successful_tools": list(self.successful_tools),
            "failed_tools": list(self.failed_tools),
            "lessons": list(self.lessons),
            "confidence": self.confidence,
        }


def summarize_experience(history: Iterable[Dict[str, Any]], lessons: Iterable[str] = ()) -> ExperienceSummary:
    success: List[str] = []
    failed: List[str] = []
    for record in history:
        tool = str(record.get("tool") or "").strip()
        status = str(record.get("status") or "").lower()
        if not tool:
            continue
        if status == "success" and tool not in success:
            success.append(tool)
        elif status == "failed" and tool not in failed:
            failed.append(tool)

    cleaned_lessons = []
    for lesson in lessons:
        value = str(lesson).strip()
        if value and value not in cleaned_lessons:
            cleaned_lessons.append(value[:500])

    total = len(success) + len(failed)
    confidence = len(success) / total if total else 0.0
    return ExperienceSummary(
        successful_tools=success[:20],
        failed_tools=failed[:20],
        lessons=cleaned_lessons[:10],
        confidence=round(confidence, 3),
    )
