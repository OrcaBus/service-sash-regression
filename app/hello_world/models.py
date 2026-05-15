"""
Pydantic models for incoming and outgoing OrcaBus events.

Incoming: WorkflowRunStateChange from orcabus.workflowmanager
Outgoing: HelloWorldEvent emitted by this service

Models can be auto-generated from JSON schema using datamodel-codegen:
  datamodel-codegen --input event-schema.json --input-file-type jsonschema --output hello_world/models.py
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


# ---- Incoming event models ----


class Workflow(BaseModel):
    orcabusId: str
    name: str
    version: str


class WorkflowRunStateChange(BaseModel):
    id: str
    version: str
    timestamp: datetime
    orcabusId: str
    portalRunId: str
    workflowRunName: str
    workflow: Workflow
    status: str
    payload: Optional[Dict[str, Any]] = None


class IncomingEvent(BaseModel):
    """EventBridge event envelope as delivered to Lambda."""

    id: Optional[str] = None
    source: str
    time: Optional[datetime] = None
    account: Optional[str] = None
    region: Optional[str] = None
    detail_type: str = Field(..., alias='detail-type')
    detail: WorkflowRunStateChange

    model_config = {'populate_by_name': True}


# ---- Outgoing event models ----


class HelloWorldEventDetail(BaseModel):
    portalRunId: str
    workflowName: str
    status: str
    message: str
    processedAt: datetime
