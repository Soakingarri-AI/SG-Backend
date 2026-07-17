"""AfroSimulator cultural-safety filter.

A lightweight guardrail that rejects cartoonish/stereotyping content before it is
persisted or surfaced. In production this is backed by a Bedrock Guardrail; the
heuristic below is the deterministic first line of defence.
"""
from __future__ import annotations

from dataclasses import dataclass

# Non-exhaustive deny list of demeaning tropes. Extend via config in prod.
_STEREOTYPE_MARKERS = {
    "primitive",
    "savage",
    "voodoo doll",
    "witch doctor",
    "jungle bunny",
    "backward tribe",
    "uncivilized",
}


@dataclass
class SafetyVerdict:
    ok: bool
    flags: list[str]


def evaluate(text: str) -> SafetyVerdict:
    lowered = text.lower()
    flags = [m for m in _STEREOTYPE_MARKERS if m in lowered]
    return SafetyVerdict(ok=not flags, flags=flags)


CULTURAL_SAFETY_SYSTEM = (
    "Portray the culture with dignity, specificity, and respect. Never use cartoonish "
    "stereotypes, demeaning tropes, or mock accents. Draw on authentic proverbs, values, "
    "and social etiquette. If a request would require a stereotype, decline gracefully."
)
