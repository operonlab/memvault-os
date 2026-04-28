"""Base Pydantic schemas — shared response/request types."""

from datetime import datetime
from math import ceil

from pydantic import BaseModel


class TimestampResponse(BaseModel):
    created_at: datetime
    updated_at: datetime


class SpaceScopedResponse(TimestampResponse):
    id: str
    space_id: str
    created_by: str | None = None


class ErrorResponse(BaseModel):
    detail: str
    code: str  # "module.error_name"
    module: str | None = None


class PaginatedResponse[T](BaseModel):
    items: list[T]
    total: int
    page: int
    page_size: int

    @property
    def pages(self) -> int:
        return ceil(self.total / self.page_size) if self.page_size > 0 else 0


class PaginationParams(BaseModel):
    page: int = 1
    page_size: int = 20
