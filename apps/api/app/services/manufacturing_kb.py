"""Loader for the local ``data/manufacturing`` reference knowledge base.

Files are JSON documents keyed by product category. The loader is cached so
repeated wizard runs don't re-read disk. Missing categories degrade gracefully
to an empty reference (the LLM then relies on general knowledge).
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

# Resolves to <repo>/data/manufacturing in dev and /data/manufacturing in the
# container (mounted read-only).
_DATA_ROOT = Path(__file__).resolve().parents[3] / "data" / "manufacturing"
_CONTAINER_ROOT = Path("/data/manufacturing")


def _root() -> Path:
    return _CONTAINER_ROOT if _CONTAINER_ROOT.exists() else _DATA_ROOT


@lru_cache(maxsize=64)
def load_reference(category: str, region: str) -> dict:
    root = _root()
    slug = category.strip().lower().replace(" ", "_")
    path = root / f"{slug}.json"
    base: dict = {}
    if path.exists():
        base = json.loads(path.read_text(encoding="utf-8"))

    regions_path = root / "regions.json"
    region_info = {}
    if regions_path.exists():
        region_info = json.loads(regions_path.read_text(encoding="utf-8")).get(region, {})

    return {"category": base, "region": region_info}
