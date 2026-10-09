"""Contracts for the first resource slice; implementations follow the architecture ADR.

Content-bearing values belong inside encrypted payloads. These identifiers are
operational metadata, not permission to access a resource.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol
from uuid import UUID


class Action(StrEnum):
    READ = "resource:read"
    WRITE = "resource:write"
    REVIEW = "memory:review"
    MANAGE_ACCESS = "space:manage_access"


@dataclass(frozen=True)
class Actor:
    """Resolved from verified credentials by an authentication adapter."""

    principal_id: UUID
    organization_id: UUID
    delegated_by: UUID | None = None


@dataclass(frozen=True)
class SpaceScope:
    organization_id: UUID
    space_id: UUID


class Authorization(Protocol):
    """Live grants are required even when a client can name a valid scope."""

    async def require(self, actor: Actor, scope: SpaceScope, action: Action) -> None:
        """Raise on absent, expired, revoked, or insufficient permission."""
        ...
