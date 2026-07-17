"""ExamFlow mixer algorithm.

Builds a custom test by sampling tagged questions across the requested years
with no duplicate overlaps, balancing the draw evenly per year and shuffling the
final order. Pure/​deterministic given a seed — easy to unit test.
"""
from __future__ import annotations

import random
from collections import defaultdict
from collections.abc import Sequence

from app.models.examflow import ExamQuestion


def mix_questions(
    pool: Sequence[ExamQuestion],
    num_questions: int,
    *,
    years: list[int] | None = None,
    seed: int | None = None,
) -> list[ExamQuestion]:
    """Return up to ``num_questions`` unique questions balanced across years."""
    rng = random.Random(seed)

    candidates = list(pool)
    if years:
        candidates = [q for q in candidates if q.year in years]

    # Bucket by year, shuffle within each bucket.
    by_year: dict[int, list[ExamQuestion]] = defaultdict(list)
    for q in candidates:
        by_year[q.year].append(q)
    for bucket in by_year.values():
        rng.shuffle(bucket)

    # Round-robin across years so the draw is evenly represented, no repeats.
    selected: list[ExamQuestion] = []
    seen: set = set()
    year_cycle = sorted(by_year.keys())
    idx = {y: 0 for y in year_cycle}

    while len(selected) < num_questions and year_cycle:
        progressed = False
        for year in list(year_cycle):
            bucket = by_year[year]
            i = idx[year]
            if i >= len(bucket):
                year_cycle.remove(year)
                continue
            idx[year] += 1
            q = bucket[i]
            if q.id in seen:
                continue
            seen.add(q.id)
            selected.append(q)
            progressed = True
            if len(selected) >= num_questions:
                break
        if not progressed:
            break

    rng.shuffle(selected)
    return selected
