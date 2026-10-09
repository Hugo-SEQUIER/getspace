"""Execution receipt for one leased background job; never an end-user credential."""

from contextvars import ContextVar
from uuid import UUID

active_job: ContextVar[tuple[UUID, UUID] | None] = ContextVar("active_memory_job", default=None)
