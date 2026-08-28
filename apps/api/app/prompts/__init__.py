"""Prompt template loader.

Prompts live as Markdown files next to this module so they can be reviewed and
diffed like documentation. Loaded once and cached; ``{slots}`` are filled by
callers via ``str.format`` on the returned text.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

_PROMPT_DIR = Path(__file__).parent


@lru_cache(maxsize=32)
def load_prompt(name: str) -> str:
    """Return the raw text of ``app/prompts/<name>.md``."""
    path = _PROMPT_DIR / f"{name}.md"
    if not path.is_file():
        raise FileNotFoundError(f"Unknown prompt template: {name!r}")
    return path.read_text(encoding="utf-8")
