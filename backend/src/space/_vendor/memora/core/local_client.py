# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
# Space adaptations are documented in ../README.md.

"""
MemoraClient - Public-facing facade requiring only an API key.

Changes added (brief):
 - Removed unused imports (curses.meta, regex.P, chromadb Where) to reduce noise.
 - Added os and OmegaConf imports for default config builder.
 - Corrected API key util import function name.
 - Consolidated duplicate add/query methods into single versions that auto-inject user_id.
 - Added explanatory comments where user_id is injected and config auto-built.
"""

from collections.abc import Callable
from pathlib import Path
from typing import Any
from typing import Any as DictConfig

from space._vendor.memora.builder.chat_memory_builder import ChatMemoryBuilder
from space._vendor.memora.builder.document_memory_builder import DocumentMemoryBuilder
from space._vendor.memora.builder.memory_builder import MemoryBuilder
from space._vendor.memora.builder.memory_builder_registry import MemoryBuilderRegistry
from space._vendor.memora.core.memory import AgentMemory, QueryMode
from space._vendor.memora.core.memory_entry import MemoryEntry
from space._vendor.memora.core.segment import Segment
from space._vendor.memora.utils.llm import ChatCompletionModel
from space._vendor.memora.utils.misc import merge_metadata


class LocalMemoraClient:
    """Client facade exposing memory operations with API key auth only.

    rationale:
    - Users supply only an API key; config is auto-built (no backend leakage).
    - user_id derived from API key is transparently injected into metadata & filters.
    """

    def __init__(self, cfg: DictConfig, user_id: str):
        """
        Initialize the memory facade.

        Args:
            cfg: Configuration object
            api_key: API key for authentication (will derive user_id)
        """
        self.cfg = cfg
        self.user_id = user_id
        self._agent_memory = AgentMemory(cfg, user_id=user_id)
        self._model_client = ChatCompletionModel(cfg)
        self.memory_builder_registry = MemoryBuilderRegistry()
        self.memory_builder_registry.register("markdown", DocumentMemoryBuilder)
        self.memory_builder_registry.register("doc", DocumentMemoryBuilder)
        self.memory_builder_registry.register("chat", ChatMemoryBuilder)
        self.memory_builder_registry.register("default", ChatMemoryBuilder)
        pass

    def _get_memory_builder(self, file_type: str) -> MemoryBuilder:
        """
        Get the appropriate memory builder for a given file type.

        Args:
            file_type: The detected file type (e.g., 'markdown', 'word', 'pdf')

        Returns:
            MemoryBuilder instance
        """
        builder_type_mapping = {
            "default": "chat",
            "chat": "chat",
            "markdown": "markdown",
            "word": "doc",
            "excel": "table",
            "powerpoint": "ppt",
            "pdf": "doc",
            "text": "doc",
            "html": "html",
            "json": "json",
            "yaml": "yaml",
            "xml": "xml",
        }
        pass
        builder_type = builder_type_mapping.get(file_type, "default")
        return self.memory_builder_registry.get(
            builder_type, self.cfg, self._agent_memory, self._model_client
        )

    def add_file(self, file_path: str | Path, metadata: dict | None = None) -> list[MemoryEntry]:
        raise ValueError("Use Space source resources for ingestion")

    def _process_file(self, file_path: str | Path) -> list[Segment]:
        raise ValueError("Use Space source resources for ingestion")

    def add(
        self,
        text: str | list[str] | list[dict[str, str]] = None,
        type: str = None,
        metadata: dict | None = None,
        progress_callback: Callable[[int, int, str], None] | None = None,
    ) -> list[MemoryEntry]:
        """Add memory content; automatically stamps user_id.

        Removed duplicate variant: user_id is always derived from API key.
        Insert or update a memory record identified by 'key'.

        Args:
            text: Text to add. Can be:
                - str: Natural language text
                - List[str]: Multiple text entries
                - List[Dict[str, str]]: Structured context with key-value pairs
            metadata: Additional metadata to store with the memory record

        Returns:
            Record ID (derived from key)
        """
        if text is None:
            raise ValueError("Text must be provided")
        segments = [Segment(content=text, segment_type="text", metadata=metadata)]
        memory_builder: MemoryBuilder = self._get_memory_builder(type)
        self._last_builder = memory_builder
        memory_entries = []
        for segment in segments:
            merged_metadata = merge_metadata(segment.metadata, metadata)
            memory_entries.extend(
                memory_builder.build(
                    segment.content, metadata=merged_metadata, progress_callback=progress_callback
                )
            )
        return memory_entries

    def get_last_build_stats(self):
        """Return build stats from the most recent add() call."""
        if hasattr(self, "_last_builder") and self._last_builder:
            return dict(self._last_builder._build_stats)
        return None

    def query(
        self,
        context: str | list[str] | list[dict[str, str]],
        top_k: int = 5,
        where: dict | None = None,
        include: list[str] | None = None,
        enable_hybrid_search: bool = False,
        enable_llm_filter: bool = False,
        query_mode: QueryMode | None = None,
        **kwargs,
    ):
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
        if query_mode is None:
            query_mode = (
                QueryMode.BOTH if self.cfg.memory.enable_cue_index else QueryMode.PRIMARY_ONLY
            )
        return self._agent_memory.query(
            context,
            top_k=top_k,
            where=where,
            query_mode=query_mode,
            include=include,
            enhance_query=self.cfg.memory.enhance_query,
            return_history=self.cfg.memory.return_history,
            enable_hybrid_search=enable_hybrid_search,
            enable_llm_filter=enable_llm_filter,
            **kwargs,
        )

    def list_memories(self, limit: int = 20) -> list[MemoryEntry]:
        """
        List all memory records for the user.

        Args:
            limit: Maximum number of records to return
        Returns:
            List of memory records as dictionaries
        """
        return self._agent_memory.list_memories(limit=limit)

    def get_user_id(self) -> str:
        return self.user_id

    def get(self, key: str) -> dict[str, Any] | None:
        """
        Retrieve a single record by its natural-language key.

        Args:
            key: Natural language key to retrieve

        Returns:
            Dict with id, metadata, document fields or None if not found
        """
        return self._agent_memory.get(key)

    def delete(self, key: str) -> None:
        """
        Delete a record by its natural-language key.

        Args:
            key: Natural language key to delete
        """
        self._agent_memory.delete(key)

    def count(self) -> int:
        """
        Get the total number of memory records stored.

        Returns:
            Total count of memory records
        """
        return self._agent_memory.count()

    def clear(self) -> None:
        """
        Clear all records in the collection.
        """
        self._agent_memory.clear()

    def delete_all(self, **kwargs) -> None:
        """
        Delete all records for param in the collection.
        """
        if kwargs is None:
            param = {}
        else:
            param = {k: v for k, v in kwargs.items() if v is not None}
        self._agent_memory.delete_all(param)
