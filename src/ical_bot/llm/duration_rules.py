"""Configurable event duration inference rules replacing hardcoded LLM heuristics."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import logging
from typing import Any, Dict, Optional, Sequence, Tuple

import config

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DurationRule:
    """Rule associating event text patterns with a default duration when end time is omitted."""

    name: str
    duration_minutes: int
    keywords: Tuple[str, ...] = field(default_factory=tuple)
    required_all_groups: Tuple[Tuple[str, ...], ...] = field(default_factory=tuple)
    prompt_note: str = ""

    def matches(self, text: str) -> bool:
        """Return True if any direct keyword or all keyword-groups match ``text``."""
        lowered = (text or "").lower()
        if not lowered:
            return False
        if any(term.lower() in lowered for term in self.keywords if term):
            return True
        if self.required_all_groups and all(
            any(opt.lower() in lowered for opt in group if opt)
            for group in self.required_all_groups
        ):
            return True
        return False

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> Optional["DurationRule"]:
        """Construct a ``DurationRule`` from a decoded JSON dictionary."""
        if not isinstance(raw, dict):
            return None
        try:
            minutes = int(raw.get("duration_minutes", 0))
        except (TypeError, ValueError):
            return None
        if minutes < 1:
            return None

        raw_keywords = raw.get("keywords") or []
        keywords = tuple(str(k).strip().lower() for k in raw_keywords if str(k).strip())

        raw_groups = raw.get("required_all_groups") or []
        groups = tuple(
            tuple(str(opt).strip().lower() for opt in grp if str(opt).strip())
            for grp in raw_groups
            if isinstance(grp, (list, tuple)) and grp
        )

        if not keywords and not groups:
            return None

        return cls(
            name=str(raw.get("name") or "custom").strip() or "custom",
            duration_minutes=minutes,
            keywords=keywords,
            required_all_groups=groups,
            prompt_note=str(raw.get("prompt_note") or "").strip(),
        )


DEFAULT_MASTERS_THESIS_RULE = DurationRule(
    name="masters_thesis_presentation",
    duration_minutes=30,
    keywords=(
        "master's thesis presentation",
        "master thesis presentation",
        "master thesis defense",
        "master's thesis defense",
        "masterarbeit präsentation",
        "masterarbeit-präsentation",
        "masterarbeitspräsentation",
        "masterarbeit vortrag",
        "masterarbeitsvortrag",
        "master-abschlussvortrag",
        "masterabschlussvortrag",
    ),
    required_all_groups=(
        ("master thesis", "master's thesis", "masterarbeit", "master-arbeit", "master"),
        ("presentation", "präsentation", "defense", "verteidigung", "vortrag", "kolloquium"),
        ("thesis", "arbeit"),
    ),
    prompt_note="master's thesis presentations take 30 minutes and not one hour",
)


def get_default_duration_minutes() -> int:
    """Return configured fallback event duration in minutes (default 60)."""
    try:
        val = int(getattr(config, "LLM_DEFAULT_DURATION_MINUTES", 60))
        return max(1, val)
    except (TypeError, ValueError):
        return 60


def load_duration_rules() -> Tuple[DurationRule, ...]:
    """Load event duration rules from ``config.LLM_DURATION_RULES_JSON`` or default rules."""
    raw_json = (getattr(config, "LLM_DURATION_RULES_JSON", "") or "").strip()
    if not raw_json:
        return (DEFAULT_MASTERS_THESIS_RULE,)
    try:
        decoded = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        logger.warning("Invalid LLM_DURATION_RULES_JSON (%s); using built-in rules", exc)
        return (DEFAULT_MASTERS_THESIS_RULE,)
    if not isinstance(decoded, list):
        logger.warning("LLM_DURATION_RULES_JSON must be a JSON array; using built-in rules")
        return (DEFAULT_MASTERS_THESIS_RULE,)
    parsed = [rule for item in decoded if (rule := DurationRule.from_dict(item)) is not None]
    if not parsed:
        return ()
    return tuple(parsed)


def infer_default_duration_minutes(
    title: str,
    description: str,
    *,
    rules: Optional[Sequence[DurationRule]] = None,
    default_minutes: Optional[int] = None,
) -> int:
    """Infer an event's default duration in minutes from title/description and configured rules."""
    active_rules = load_duration_rules() if rules is None else rules
    combined = f"{title or ''} {description or ''}"
    for rule in active_rules:
        if rule.matches(combined):
            return rule.duration_minutes
    return get_default_duration_minutes() if default_minutes is None else max(1, int(default_minutes))


def render_duration_prompt_instructions(rules: Optional[Sequence[DurationRule]] = None) -> str:
    """Format human-readable duration instructions derived from active rules."""
    active_rules = load_duration_rules() if rules is None else rules
    notes = [rule.prompt_note for rule in active_rules if rule.prompt_note]
    if not notes:
        return ""
    return "; ".join(notes)


__all__ = [
    "DEFAULT_MASTERS_THESIS_RULE",
    "DurationRule",
    "get_default_duration_minutes",
    "infer_default_duration_minutes",
    "load_duration_rules",
    "render_duration_prompt_instructions",
]

