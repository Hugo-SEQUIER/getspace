# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
# Space adaptations are documented in ../README.md.

"""
Semantic Memory Retrieval

This module provides a semantic search-based memory retrieval implementation
that extends the BaseMemoryRetrieval class.
"""

from typing import Any as DictConfig

from space._vendor.memora.core.memory import AgentMemory, QueryMode
from space._vendor.memora.core.memory_entry import MemoryEntry
from space._vendor.memora.retriever.base_retriever import BaseMemoryRetriever


class SemanticRetriever(BaseMemoryRetriever):
    """
    Semantic memory retrieval using vector similarity search.

    This class implements memory retrieval using semantic similarity between
    the query and stored memories. It leverages the AgentMemory query interface
    for vector-based search.

    Features:
    - Vector similarity search
    - Score-based filtering
    - Deduplication
    - Query validation

    Example:
        retriever = SemanticRetrieval(cfg, user_id="user123")
        result = retriever.retrieve("What is the user's favorite color?")
        for memory in result.memories:
            print(f"{memory.value} (score: {memory.score})")
    """

    def __init__(self, cfg: DictConfig, memory_client: AgentMemory | None = None):
        """
        Initialize semantic retrieval.

        Args:
            cfg: Configuration object
            memory_client: Optional pre-initialized memory client
            user_id: User identifier
        """
        super().__init__(cfg)
        self.memory_client = memory_client
        self.top_k = self.cfg.memory.get("top_k", 30)
        self.enable_hybrid_search = self.cfg.memory.get("enable_hybrid_search", False)
        self.enable_llm_filter = self.cfg.retrieval.get("enable_llm_filter", False)
        if self.cfg.memory.get("enable_cue_index", False):
            self.query_mode = QueryMode.BOTH
        else:
            self.query_mode = QueryMode.PRIMARY_ONLY

    def retrieve(
        self,
        query: str,
        top_k: int | None = None,
        enable_hybrid_search: bool | None = None,
        enable_llm_filter: bool | None = None,
        query_mode: QueryMode | None = None,
        latency_tracker=None,
        **kwargs,
    ) -> list[MemoryEntry]:
        """
        Retrieve memories using semantic similarity search.

        Args:
            query: Natural language query
            top_k: Number of results to return (overrides config)
            enable_hybrid_search: Whether to enable hybrid search
            enable_llm_filter: Whether to enable LLM-based filtering
            latency_tracker: Optional LatencyTracker for performance measurement
            **kwargs: Additional parameters

        Returns:
            List of retrieved memories
        """
        if top_k is None:
            top_k = self.top_k
        if enable_hybrid_search is None:
            enable_hybrid_search = self.enable_hybrid_search
        if enable_llm_filter is None:
            enable_llm_filter = self.enable_llm_filter
        if query_mode is None:
            query_mode = self.query_mode
        memories = self.memory_client.query(
            query,
            top_k=top_k,
            enable_hybrid_search=enable_hybrid_search,
            enable_llm_filter=enable_llm_filter,
            query_mode=query_mode,
            # AgentMemory applies primary/cue type constraints to each branch.
            where=None,
            latency_tracker=latency_tracker,
        )
        return memories
