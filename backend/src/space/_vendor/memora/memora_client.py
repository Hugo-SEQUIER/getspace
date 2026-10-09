# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
# Space adaptations are documented in ../README.md.

"""
MemoraClient - Public-facing facade requiring only an API key or local config.
"""

from collections.abc import Callable
from pathlib import Path
from typing import Any
from typing import Any as DictConfig

from space._vendor.memora.core.local_client import LocalMemoraClient
from space._vendor.memora.core.memory_entry import MemoryEntry
from space._vendor.memora.retriever.prompted_policy_retriever import PromptedPolicyRetriever
from space._vendor.memora.retriever.semantic_retriever import SemanticRetriever


class MemoraClient:
    """Client facade exposing memory operations with API key auth only.

    rationale:
    - Users supply only an API key; config is auto-built (no backend leakage).
    - user_id derived from API key is transparently injected into metadata & filters.
    """

    def __init__(
        self, cfg: DictConfig | None = None, user_id: str = None, api_key: str | None = None
    ):
        """
        Initialize the memory facade.

        Args:
            cfg: Configuration object
            api_key: API key for authentication (will derive user_id)
        """
        if api_key:
            raise ValueError("Space requires its authorized local adapter")
        else:
            if not cfg or not user_id:
                raise ValueError("For local MemoraClient, both cfg and user_id must be provided.")
            self._client = LocalMemoraClient(cfg, user_id)

    def add(
        self,
        text: str | list[str] | list[dict[str, str]] = None,
        type: str = "default",
        metadata: dict | None = None,
        progress_callback: Callable[[int, int, str], None] | None = None,
    ) -> list[MemoryEntry]:
        """Add memory content; automatically stamps user_id.

        Removed duplicate variant: user_id is always derived from API key.
        Insert or update a memory record identified by 'key'.

        Args:
            context: Context to add. Can be:
                - str: Natural language text
                - List[str]: Multiple text entries
                - List[Dict[str, str]]: Structured context with key-value pairs
            metadata: Additional metadata to store with the memory record
            progress_callback: Optional callback for progress updates

        Returns:
            Record ID (derived from key)
        """
        return self._client.add(
            text, type=type, metadata=metadata, progress_callback=progress_callback
        )

    def get_last_build_stats(self):
        """Return build stats from the most recent add() call."""
        return self._client.get_last_build_stats()

    def add_file(self, file_path: str | Path, metadata: dict | None = None) -> list[MemoryEntry]:
        raise ValueError("Use Space source resources for ingestion")

    def query(
        self,
        context: str | list[str] | list[dict[str, str]],
        top_k: int = 5,
        where: dict | None = None,
        include: list[str] | None = None,
        enable_hybrid_search: bool = False,
        enable_llm_filter: bool = False,
        query_mode=None,
        **kwargs,
    ) -> list[MemoryEntry]:
        """
        Vector search by context information to find similar memories.

        Args:
            context: Context to search for. Can be:
                - str: Natural language query text
                - List[str]: Multiple query strings
                - List[Dict[str, str]]: Structured context with key-value pairs
            k: Number of results to return
            where: Filter conditions for metadata-based filtering
            include: Fields to include in results (e.g., ["metadatas", "distances"])
            enable_hybrid_search: Whether to enable hybrid search combining semantic + keyword search
            enable_llm_filter: Whether to use LLM to filter irrelevant memories
            query_mode: Query mode (ORIGINAL, PRIMARY_ONLY, CUE_ONLY, or BOTH)
            filtering: Whether to apply additional filtering on the retrieved memories

        Returns:
            Backend-specific result object containing matching memories
        """
        return self._client.query(
            context,
            top_k=top_k,
            where=where,
            include=include,
            enable_hybrid_search=enable_hybrid_search,
            enable_llm_filter=enable_llm_filter,
            query_mode=query_mode,
            **kwargs,
        )

    def advance_query(
        self,
        context: str | list[str] | list[dict[str, str]],
        top_k: int = 5,
        query_type="prompt",
        checkpoint_path: str | None = None,
        latency_tracker=None,
    ) -> list[MemoryEntry]:
        """
        Advanced query with different retrieval strategies.

        Args:
            context: Context to search for. Can be:
                - str: Natural language query text
                - List[str]: Multiple query strings
                - List[Dict[str, str]]: Structured context with key-value pairs
            top_k: Number of results to return
            query_type: Type of query strategy ("semantic", "prompt", or "grpo")
            checkpoint_path: Path to GRPO-trained LoRA checkpoint (required for "grpo")
            latency_tracker: Optional LatencyTracker for performance measurement

        Returns:
            Backend-specific result object containing matching memories
        """
        if query_type not in ["semantic", "prompt", "grpo"]:
            raise ValueError(
                f"Unsupported query_type: {query_type}. Use 'semantic', 'prompt', or 'grpo'."
            )
        if query_type == "semantic":
            retriever = SemanticRetriever(self._client.cfg, memory_client=self._client)
        elif query_type == "prompt":
            retriever = PromptedPolicyRetriever(self._client.cfg, memory_client=self._client)
        elif query_type == "grpo":
            if not checkpoint_path:
                raise ValueError("checkpoint_path is required for query_type='grpo'")
            raise ValueError("Research GRPO checkpoints are not a Space runtime provider")
        return retriever.retrieve(query=context, top_k=top_k, latency_tracker=latency_tracker)

    def list_memories(self, limit: int = 20) -> list[MemoryEntry]:
        """
        List memories in the collection.

        Args:
            limit: Maximum number of memories to return

        Returns:
            Dict with total count and list of memory records
        """
        return self._client.list_memories(limit=limit)

    def get(self, key: str) -> dict[str, Any] | None:
        """
        Retrieve a single record by its natural-language key.

        Args:
            key: Natural language key to retrieve

        Returns:
            Dict with id, metadata, document fields or None if not found
        """
        return self._client.get(key)

    def delete(self, key: str) -> None:
        """
        Delete a record by its natural-language key.

        Args:
            key: Natural language key to delete
        """
        self._client.delete(key)

    def count(self) -> int:
        """
        Get the total number of memory records stored.

        Returns:
            Total count of memory records
        """
        return self._client.count()

    def clear(self) -> None:
        """
        Clear all records in the collection.
        """
        self._client.clear()

    def delete_all(self, **kwargs) -> None:
        """
        Delete all records for param in the collection.
        """
        self._client.delete_all(**kwargs)
