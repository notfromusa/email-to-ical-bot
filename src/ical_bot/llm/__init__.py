"""LLM schema definitions and configurable duration inference rules."""

from ical_bot.llm.duration_rules import (
    DEFAULT_MASTERS_THESIS_RULE,
    DurationRule,
    get_default_duration_minutes,
    infer_default_duration_minutes,
    load_duration_rules,
    render_duration_prompt_instructions,
)
from ical_bot.llm.schemas import (
    EVENT_EXTRACTION_JSON_SCHEMA,
    EventCandidate,
    ExtractionResult,
)

__all__ = [
    "DEFAULT_MASTERS_THESIS_RULE",
    "DurationRule",
    "EVENT_EXTRACTION_JSON_SCHEMA",
    "EventCandidate",
    "ExtractionResult",
    "get_default_duration_minutes",
    "infer_default_duration_minutes",
    "load_duration_rules",
    "render_duration_prompt_instructions",
]
