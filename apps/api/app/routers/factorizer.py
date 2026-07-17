"""Factorizer — industrial factory-setup wizard router.

Takes wizard inputs, parses them against the local ``data/manufacturing``
reference database, and returns a comprehensive 15-section markdown factory plan
plus structured MachineList / ProcessFlow / CostBreakdown data for the UI.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.rate_limit import rate_limit
from app.deps import get_current_user
from app.models.factorizer import FactoryPlan
from app.models.user import User
from app.schemas.modules import FactoryPlanStructured, FactoryWizardInput
from app.services.ai_service import ai_service
from app.services.manufacturing_kb import load_reference

router = APIRouter(prefix="/factorizer", tags=["factorizer"], dependencies=[Depends(rate_limit)])

_SECTIONS = [
    "Executive Summary",
    "Market & Product Overview",
    "Site & Facility Requirements",
    "Machinery & Equipment",
    "Process Flow",
    "Raw Materials & Sourcing",
    "Bill of Materials",
    "Labour & Staffing",
    "Automation Strategy",
    "Utilities & Power",
    "Regulatory & Compliance",
    "Quality Control",
    "Cost Breakdown & Capex",
    "Timeline & Milestones",
    "Risks & Mitigations",
]


@router.post("/plans")
async def generate_plan(
    payload: FactoryWizardInput,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    reference = load_reference(payload.category, payload.region)

    system = (
        "You are an industrial manufacturing consultant. Produce a comprehensive factory "
        "setup plan as GitHub-flavoured markdown with EXACTLY these 15 sections, in order: "
        + "; ".join(f"{i + 1}. {s}" for i, s in enumerate(_SECTIONS))
        + ". Ground recommendations in the provided reference data and the user's budget, "
        "automation level, and region."
    )
    prompt = (
        f"Wizard inputs: {payload.model_dump()}\n\n"
        f"Reference data: {reference}"
    )
    plan = await ai_service.complete(
        system=system,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=4000,
    )

    # Second, cheaper structured pass to power the interactive UI components.
    structured, _ = await ai_service.complete_json(
        system="Extract machines, process flow steps, and cost breakdown from the factory plan.",
        messages=[{"role": "user", "content": plan.text}],
        schema=FactoryPlanStructured,
        temperature=0.2,
    )

    record = FactoryPlan(
        user_id=user.id,
        inputs=payload.model_dump(),
        target_product=payload.target_product,
        plan_markdown=plan.text,
        machines=[m.model_dump() for m in structured.machines],
        process_flow=structured.process_flow,
        cost_breakdown=[c.model_dump() for c in structured.cost_breakdown],
    )
    db.add(record)
    await db.flush()

    return {
        "id": record.id,
        "plan_markdown": record.plan_markdown,
        "machines": record.machines,
        "process_flow": record.process_flow,
        "cost_breakdown": record.cost_breakdown,
    }
