"""Typed contracts exchanged with the model (validated by OpenMinion's output_type)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class Evidence(BaseModel):
    path: str = Field(description="Repo-relative file path")
    quote: str = Field(default="", description="Short verbatim snippet from that file")


class CriterionScore(BaseModel):
    criterion: str
    score: int = Field(ge=0, le=10, description="0 = missing, 10 = clearly excellent")
    evidence: list[Evidence] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    fixes: list[str] = Field(default_factory=list, description="Concrete next actions")


class Scorecard(BaseModel):
    project_summary: str
    criteria: list[CriterionScore]
    top_risks: list[str] = Field(default_factory=list)


class Challenge(BaseModel):
    criterion: str
    suggested_score: int = Field(ge=0, le=10)
    reason: str


class SkepticReview(BaseModel):
    verdict: str = Field(description="agree | too_generous | too_harsh")
    challenges: list[Challenge] = Field(default_factory=list)
