from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class PatternCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=256)
    category: str = Field(..., min_length=1, max_length=128)
    intent: str = Field(..., min_length=1)
    structure: str = Field(..., min_length=1)
    when_to_use: str = Field(..., min_length=1)
    anti_patterns: str = Field("", max_length=8192)
    prerequisites: str = Field("", max_length=8192)
    references: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class PatternUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=256)
    category: str | None = Field(None, min_length=1, max_length=128)
    intent: str | None = Field(None, min_length=1)
    structure: str | None = Field(None, min_length=1)
    when_to_use: str | None = Field(None, min_length=1)
    anti_patterns: str | None = None
    prerequisites: str | None = None
    references: list[str] | None = None
    tags: list[str] | None = None


class PatternOut(BaseModel):
    id: str
    name: str
    slug: str
    category: str
    intent: str
    structure: str
    when_to_use: str
    anti_patterns: str
    prerequisites: str
    references: list[str]
    tags: list[str]
    version: int
    is_canonical: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class PatternBulkImport(BaseModel):
    patterns: list[PatternCreate]


class PatternSearchQuery(BaseModel):
    query: str = Field(..., min_length=1, max_length=2048)
    category: str | None = None
    tags: list[str] | None = None
    top_k: int = Field(5, ge=1, le=50)


class PatternSearchResult(BaseModel):
    pattern: PatternOut
    score: float
    snippet: str