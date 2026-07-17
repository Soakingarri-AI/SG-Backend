"""AfroSimulator — cultural multi-agent simulation router.

A simulation is created immediately (status=pending) and the multi-turn agent
conversation runs in a FastAPI ``BackgroundTasks`` coordinator (mapped to an SQS
+ worker task on ECS in production). Each generated turn passes the cultural
safety filter before being persisted.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import AsyncSessionLocal, get_db
from app.core.rate_limit import rate_limit
from app.deps import get_current_user
from app.models.afro import (
    AgentPersona,
    Culture,
    Simulation,
    SimulationStatus,
    SimulationTurn,
)
from app.models.user import User
from app.schemas.modules import SimulationCreate
from app.services import cultural_safety
from app.services.ai_service import ai_service

router = APIRouter(prefix="/afro", tags=["afrosimulator"], dependencies=[Depends(rate_limit)])


@router.post("/simulations", status_code=status.HTTP_202_ACCEPTED)
async def create_simulation(
    payload: SimulationCreate,
    background: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    sim = Simulation(
        user_id=user.id,
        topic=payload.topic,
        participant_cultures=[c for c in payload.cultures],
        max_turns=payload.max_turns,
        status=SimulationStatus.pending,
    )
    db.add(sim)
    await db.flush()
    sim_id = sim.id

    background.add_task(_run_simulation, sim_id)
    return {"simulation_id": sim_id, "status": sim.status.value}


@router.get("/simulations/{simulation_id}")
async def get_simulation(
    simulation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    sim = await db.get(
        Simulation, simulation_id, options=[selectinload(Simulation.turns)]
    )
    if not sim or sim.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Simulation not found")
    return {
        "id": sim.id,
        "status": sim.status.value,
        "topic": sim.topic,
        "summary": sim.summary,
        "turns": [
            {
                "turn_index": t.turn_index,
                "culture": t.culture.value,
                "speaker": t.speaker,
                "content": t.content,
                "proverbs": t.proverbs,
            }
            for t in sim.turns
        ],
    }


async def _run_simulation(simulation_id: uuid.UUID) -> None:
    """Background coordinator: drive a round-robin multi-agent conversation."""
    async with AsyncSessionLocal() as db:
        sim = await db.get(Simulation, simulation_id)
        if sim is None:
            return
        sim.status = SimulationStatus.running
        await db.commit()

        try:
            personas = await _load_personas(db, sim.participant_cultures)
            transcript: list[str] = []
            order = [Culture(c) for c in sim.participant_cultures]

            for turn in range(sim.max_turns):
                culture = order[turn % len(order)]
                persona = personas[culture]
                context = "\n".join(transcript[-6:])
                result = await ai_service.complete(
                    system=f"{persona.system_prompt}\n\n{cultural_safety.CULTURAL_SAFETY_SYSTEM}",
                    messages=[
                        {
                            "role": "user",
                            "content": (
                                f"Topic: {sim.topic}\nConversation so far:\n{context}\n\n"
                                f"Respond in character as {persona.name}. Weave in an authentic "
                                "proverb where natural."
                            ),
                        }
                    ],
                    temperature=0.85,
                )

                verdict = cultural_safety.evaluate(result.text)
                content = result.text if verdict.ok else "[content withheld by safety filter]"
                db.add(
                    SimulationTurn(
                        simulation_id=sim.id,
                        turn_index=turn,
                        culture=culture,
                        speaker=persona.name,
                        content=content,
                        proverbs=_extract_proverbs(content),
                        safety_flags={"flags": verdict.flags},
                    )
                )
                transcript.append(f"{persona.name}: {content}")
                await db.commit()

            # Auto summary.
            summary = await ai_service.complete(
                system="Summarize this cross-cultural dialogue in 3 neutral sentences.",
                messages=[{"role": "user", "content": "\n".join(transcript)}],
                max_tokens=300,
            )
            sim.summary = summary.text
            sim.status = SimulationStatus.completed
            await db.commit()
        except Exception:
            sim.status = SimulationStatus.failed
            await db.commit()
            raise


async def _load_personas(
    db: AsyncSession, cultures: list[str]
) -> dict[Culture, AgentPersona]:
    rows = (
        await db.scalars(
            select(AgentPersona).where(
                AgentPersona.culture.in_([Culture(c) for c in cultures])
            )
        )
    ).all()
    return {p.culture: p for p in rows}


def _extract_proverbs(text: str) -> list[str]:
    # Naive extraction: quoted spans are treated as proverbs.
    import re

    return re.findall(r"[\"“](.+?)[\"”]", text)[:3]
