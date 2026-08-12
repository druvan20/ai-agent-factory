from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class ErrorDetail(BaseModel):
    field: str | None = None
    message: str


class ErrorResponse(BaseModel):
    """Structured JSON error envelope used for all error responses."""
    status: str = "error"
    code: int
    message: str
    details: list[ErrorDetail] = Field(default_factory=list)


class SuccessResponse(BaseModel, Generic[T]):
    status: str = "success"
    data: T


class PaginatedResponse(BaseModel, Generic[T]):
    status: str = "success"
    data: list[T]
    total: int
    page: int
    page_size: int
    total_pages: int


class PaginationParams(BaseModel):
    page: int = Field(1, ge=1)
    page_size: int = Field(20, ge=1, le=100)

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size