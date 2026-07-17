"""ExamFlow — practice exam generation, submission, and AI correction."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.core.rate_limit import rate_limit
from app.deps import get_current_user
from app.models.examflow import (
    ExamBoard,
    ExamQuestion,
    ExamSession,
    ExamSessionQuestion,
)
from app.models.user import User
from app.schemas.modules import ExamAnswerSubmit, ExamGenerateRequest
from app.services.ai_service import ai_service
from app.services.exam_mixer import mix_questions

router = APIRouter(prefix="/examflow", tags=["examflow"], dependencies=[Depends(rate_limit)])


@router.post("/sessions", status_code=status.HTTP_201_CREATED)
async def generate_session(
    payload: ExamGenerateRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    pool = (
        await db.scalars(
            select(ExamQuestion).where(
                ExamQuestion.board == ExamBoard(payload.board),
                ExamQuestion.subject == payload.subject,
            )
        )
    ).all()
    if not pool:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No questions match that filter")

    picked = mix_questions(pool, payload.num_questions, years=payload.years)

    session = ExamSession(
        user_id=user.id,
        board=ExamBoard(payload.board),
        subject=payload.subject,
        duration_seconds=payload.duration_seconds,
        config={"requested": payload.num_questions, "delivered": len(picked)},
    )
    db.add(session)
    await db.flush()

    for pos, q in enumerate(picked):
        db.add(
            ExamSessionQuestion(session_id=session.id, question_id=q.id, position=pos)
        )
    await db.flush()

    return {
        "session_id": session.id,
        "duration_seconds": session.duration_seconds,
        "questions": [
            {
                "position": pos,
                "id": q.id,
                "stem": q.stem,
                "options": q.options,
            }
            for pos, q in enumerate(picked)
        ],
    }


@router.post("/sessions/{session_id}/submit")
async def submit_session(
    session_id: uuid.UUID,
    payload: ExamAnswerSubmit,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    session = await db.get(
        ExamSession, session_id, options=[selectinload(ExamSession.questions).selectinload(ExamSessionQuestion.question)]
    )
    if not session or session.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found")
    if session.is_submitted:
        raise HTTPException(status.HTTP_409_CONFLICT, "Already submitted")

    correct = 0
    corrections: list[dict] = []
    for sq in session.questions:
        chosen = payload.answers.get(sq.id)
        sq.selected_option = chosen
        sq.is_correct = chosen == sq.question.correct_option
        if sq.is_correct:
            correct += 1
        else:
            corrections.append(
                {
                    "position": sq.position,
                    "stem": sq.question.stem,
                    "your_answer": chosen,
                    "correct_answer": sq.question.correct_option,
                    "explanation": sq.question.explanation,
                }
            )

    session.is_submitted = True
    session.score = round(100 * correct / max(len(session.questions), 1))

    return {
        "score": session.score,
        "correct": correct,
        "total": len(session.questions),
        "corrections": corrections,
    }


@router.get("/sessions/{session_id}/tutor/{position}")
async def tutor_explanation(
    session_id: uuid.UUID,
    position: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """Step-by-step AI tutoring for a single question in a completed session."""
    session = await db.get(
        ExamSession, session_id, options=[selectinload(ExamSession.questions).selectinload(ExamSessionQuestion.question)]
    )
    if not session or session.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found")
    sq = next((s for s in session.questions if s.position == position), None)
    if sq is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Question not found")

    q = sq.question
    result = await ai_service.complete(
        system=(
            "You are an encouraging exam tutor. Explain the solution step by step, "
            "then state why the correct option is right and the common distractors are wrong."
        ),
        messages=[
            {
                "role": "user",
                "content": (
                    f"Question: {q.stem}\nOptions: {q.options}\n"
                    f"Correct: {q.correct_option}\nStudent chose: {sq.selected_option}"
                ),
            }
        ],
    )
    return {"position": position, "explanation": result.text}
