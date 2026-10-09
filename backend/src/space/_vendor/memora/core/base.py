# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
# Space adaptations are documented in ../README.md.

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any
from typing import Any as Where

from space._vendor.memora.core.memory_entry import MemoryEntry


class MemoryBase(ABC):
    """
    Abstract base class for agent memory.

    Contract:
      - The 'key' is the natural-language index term to be embedded by the backend.
      - Implementations should store the original key and value (attribute) in metadata.
      - IDs may be derived deterministically from the key (e.g., hash) or provided by the backend.

    Implementations should be idempotent for the same key (e.g., via upsert semantics).
    """

    @abstractmethod
    def add(self, entry: MemoryEntry) -> str:
        """
        Insert a memory entry.
        Returns a stable record ID (e.g., hash(key)).
        """
        raise NotImplementedError

    @abstractmethod
    def query(
        self, query_key: str, k: int = 5, where: Any = None, include: list[str] | None = None
    ) -> Any:
        """
        Vector search by a natural-language query key.
        Returns a backend-specific result object (e.g., Chroma query dict).
        """
        raise NotImplementedError

    @abstractmethod
    def get(self, key: str, user_id: str) -> dict[str, Any] | None:
        """
        Retrieve a single record by its natural-language key.
        Should map key -> record_id internally (e.g., via hash) and return a dict with:
          { "id": <str>, "metadata": <dict or None>, "document": <str or None> }
        Returns None if not found.
        """
        raise NotImplementedError

    @abstractmethod
    def delete(self, key: str) -> None:
        """
        Delete a record by its natural-language key.
        Should be a no-op if the record does not exist.
        """
        raise NotImplementedError

    def clear(self) -> None:
        """
        Clear all records in the collection.
        Optional; may raise NotImplementedError if not supported.
        """
        raise NotImplementedError


class MemoryStoreBase(ABC):
    """
    Abstract base class for memory store implementations.

    This class defines the contract for different memory storage backends
    such as local ChromaDB, HTTP-based storage, or other vector databases.

    Memory stores are responsible for:
    - Storing and retrieving memory records with vector embeddings
    - Supporting similarity search via vector queries
    - Managing metadata and document storage
    - Providing CRUD operations for memory records
    """

    @abstractmethod
    def upsert(self, index: str, value: str, metadata: dict[str, Any] | None = None) -> str:
        """
        Insert or update a memory record identified by 'index'.

        Args:
            index: Natural language index to be embedded
            value: Value/content to store
            metadata: Additional metadata to store

        Returns:
            Record ID (derived from index)
        """
        raise NotImplementedError

    @abstractmethod
    def query(
        self, query: str, k: int = 5, where: Where | None = None, include: list[str] | None = None
    ) -> list[MemoryEntry]:
        """
        Vector search by context information to find similar memories.

        Args:
            query: Query context string to search for
            k: Number of results to return
            where: Filter conditions for metadata-based filtering
            include: Fields to include in results

        Returns:
            Backend-specific result object
        """
        raise NotImplementedError

    @abstractmethod
    def get(self, key: str, user_id: str) -> MemoryEntry:
        """
        Retrieve a single record by its natural-language key.

        Args:
            key: Natural language key to retrieve

        Returns:
            Dict with id, metadata, document fields or None if not found
        """
        raise NotImplementedError

    @abstractmethod
    def delete(self, key: str) -> None:
        """
        Delete a record by its natural-language key.

        Args:
            key: Natural language key to delete
        """
        raise NotImplementedError

    @abstractmethod
    def list_memories(self, limit: int = 10) -> dict[str, Any]:
        """
        List memories in the collection.

        Args:
            limit: Max number of records to return

        Returns:
            Dict containing memory records
        """
        raise NotImplementedError

    @abstractmethod
    def count(self) -> int:
        """
        Get the number of records in the collection.

        Returns:
            Number of records in the collection
        """
        raise NotImplementedError

    @abstractmethod
    def clear(self) -> None:
        """
        Clear all records in the collection.
        """
        raise NotImplementedError
