"""JSON Schema definition and typed models for LLM event extraction."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

EVENT_EXTRACTION_JSON_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "has_event": {"type": "boolean"},
        "events": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "start_datetime": {"type": "string"},
                    "end_datetime": {"type": "string"},
                    "location": {"type": "string"},
                },
                "required": [
                    "title",
                    "description",
                    "start_datetime",
                    "end_datetime",
                    "location",
                ],
                "additionalProperties": False,
            },
        },
    },
    "required": ["has_event", "events"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class EventCandidate:
    """Typed structured representation of a single extracted calendar event."""

    title: str
    description: str
    start_datetime: str
    end_datetime: str
    location: str = ""

    def to_dict(self) -> Dict[str, str]:
        """Convert candidate back to the dictionary structure used by callers."""
        return {
            "title": self.title,
            "description": self.description,
            "start_datetime": self.start_datetime,
            "end_datetime": self.end_datetime,
            "location": self.location,
        }


@dataclass(frozen=True)
class ExtractionResult:
    """Validated top-level response returned by the LLM extraction seam."""

    has_event: bool
    events: List[EventCandidate] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize extraction outcome into a plain dictionary."""
        return {
            "has_event": self.has_event,
            "events": [ev.to_dict() for ev in self.events],
        }


__all__ = ["EVENT_EXTRACTION_JSON_SCHEMA", "EventCandidate", "ExtractionResult"]

