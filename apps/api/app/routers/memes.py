"""Meme Generator — expectation-vs-reality humor router.

Uses the strict structured-output path so the model always returns
``{expectation, reality, caption}`` with reality as the polar opposite of
expectation.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.rate_limit import rate_limit
from app.deps import get_current_user
from app.models.meme import Meme
from app.models.user import User
from app.schemas.modules import MemeRequest, MemeStructured
from app.services.ai_service import ai_service

router = APIRouter(prefix="/memes", tags=["memes"], dependencies=[Depends(rate_limit)])

_STYLE_FLAVOUR = {
    "nigerian_student": "Nigerian student humor — relatable campus, JAMB, and hustle references.",
}


@router.post("")
async def generate_meme(
    payload: MemeRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    flavour = _STYLE_FLAVOUR.get(payload.style, "localized African student humor.")
    system = (
        "You are a witty meme writer. Produce an expectation-vs-reality meme where the "
        f"'reality' is the polar OPPOSITE of the 'expectation'. Style: {flavour} "
        "Keep each field under 140 characters and punchy."
    )
    structured, usage = await ai_service.complete_json(
        system=system,
        messages=[{"role": "user", "content": payload.prompt}],
        schema=MemeStructured,
        temperature=0.9,
    )

    meme = Meme(
        user_id=user.id,
        prompt=payload.prompt,
        style=payload.style,
        expectation=structured.expectation,
        reality=structured.reality,
        caption=structured.caption,
    )
    db.add(meme)
    await db.flush()

    return {
        "id": meme.id,
        "expectation": meme.expectation,
        "reality": meme.reality,
        "caption": meme.caption,
        "tokens": usage.total,
    }
