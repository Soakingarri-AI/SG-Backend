"""InfiniteParts — parametric 3D part specification router.

Turns a natural-language design prompt into a validated parametric envelope
(``PartParameters``) that the React-Three-Fiber preview renders live and
adjusts. Ships a mandatory manufacturing-validation notice.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.rate_limit import rate_limit
from app.deps import get_current_user
from app.models.infiniteparts import PartSpecification
from app.models.user import User
from app.schemas.modules import PartParameters, PartRequest
from app.services.ai_service import ai_service

router = APIRouter(
    prefix="/infiniteparts", tags=["infiniteparts"], dependencies=[Depends(rate_limit)]
)

SAFETY_NOTICE = (
    "⚠️ Generated dimensions are AI estimates. Direct G-code / CNC compilation requires "
    "manual physical measurement validation before machining. Do not manufacture from "
    "these values without verifying tolerances against your material and equipment."
)


@router.post("")
async def specify_part(
    payload: PartRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    system = (
        "You are a mechanical design assistant. Convert the design request into strict "
        "parametric dimensions. Use millimetres unless the user specifies otherwise. "
        "Choose sensible engineering defaults and realistic tolerances."
    )
    params, usage = await ai_service.complete_json(
        system=system,
        messages=[{"role": "user", "content": payload.prompt}],
        schema=PartParameters,
        temperature=0.3,
    )

    spec = PartSpecification(
        user_id=user.id,
        prompt=payload.prompt,
        name=params.name,
        parameters=params.model_dump(),
    )
    db.add(spec)
    await db.flush()

    return {
        "id": spec.id,
        "parameters": params.model_dump(),
        "notice": SAFETY_NOTICE,
        "tokens": usage.total,
    }
