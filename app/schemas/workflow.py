from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel, Field

from app.models.workflow_run import WorkflowType, RunStatus


class StartWorkflowRequest(BaseModel):
    workflow_type: WorkflowType = WorkflowType.REQUIREMENTS
    max_clarification_rounds: int = Field(3, ge=1, le=10)


class WorkflowRunOut(BaseModel):
    id: str
    project_id: str
    workflow_type: WorkflowType
    status: RunStatus
    thread_id: str
    clarification_round: int
    max_clarification_rounds: int
    artifact_md: str | None
    artifact_json: str | None
    bundle_path: str | None = None
    current_node: str | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    error_message: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ClarificationResponse(BaseModel):
    answers: dict[str, str] = Field(..., min_length=1)


class ApprovalRequest(BaseModel):
    approved: bool | None = True
    feedback: str = ""


class RejectRequest(BaseModel):
    feedback: str = Field(..., min_length=1)


class StartPlanningRequest(BaseModel):
    """Request body for starting Workflow 2 (planning)."""
    requirements_run_id: str = Field(..., description="ID of the approved W1 run whose spec feeds W2")


class StartCodegenRequest(BaseModel):
    """Request body for starting Workflow 3 (code generation)."""
    planning_run_id: str = Field(..., description="ID of the approved W2 run whose task list feeds W3")