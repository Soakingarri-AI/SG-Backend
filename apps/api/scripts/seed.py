"""Idempotent dev seed: cultural personas + a couple of exam questions.

Run with:  python -m scripts.seed
"""
from __future__ import annotations

import asyncio

from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.afro import AgentPersona, Culture
from app.models.examflow import ExamBoard, ExamQuestion

PERSONAS = [
    (
        Culture.yoruba,
        "Adéyẹmí",
        "You are a warm Yoruba elder from Ọ̀yọ́. You value ẹ̀yà (lineage), respect, and "
        "Ifá wisdom. You greet elaborately and cite Yoruba proverbs (òwe) naturally.",
        {"greeting": "Ẹ n lẹ o", "register": "proverb-rich"},
    ),
    (
        Culture.igbo,
        "Chidiebere",
        "You are an enterprising Igbo trader from Onitsha. You prize industry, community "
        "(umunna), and negotiation. You pepper speech with Igbo proverbs (ilu).",
        {"greeting": "Kedu", "register": "pragmatic"},
    ),
    (
        Culture.hausa,
        "Amina",
        "You are a courteous Hausa scholar from Kano. You value hospitality (karɓar baƙi), "
        "patience (haƙuri), and learning. You use Hausa proverbs (karin magana).",
        {"greeting": "Sannu", "register": "measured"},
    ),
]


async def main() -> None:
    async with AsyncSessionLocal() as db:
        for culture, name, prompt, schedule in PERSONAS:
            exists = await db.scalar(
                select(AgentPersona).where(AgentPersona.culture == culture)
            )
            if not exists:
                db.add(
                    AgentPersona(
                        culture=culture,
                        name=name,
                        system_prompt=prompt,
                        memory_buffer=[],
                        linguistic_schedule=schedule,
                    )
                )

        sample = await db.scalar(select(ExamQuestion).limit(1))
        if not sample:
            db.add(
                ExamQuestion(
                    board=ExamBoard.jamb,
                    subject="Mathematics",
                    year=2022,
                    topic="Algebra",
                    difficulty=2,
                    stem="If 2x + 3 = 11, what is x?",
                    options=[
                        {"key": "A", "text": "2"},
                        {"key": "B", "text": "4"},
                        {"key": "C", "text": "5"},
                        {"key": "D", "text": "7"},
                    ],
                    correct_option="B",
                    explanation="2x = 8, so x = 4.",
                    tags=["algebra", "linear-equations"],
                )
            )
        await db.commit()
    print("Seed complete.")


if __name__ == "__main__":
    asyncio.run(main())
