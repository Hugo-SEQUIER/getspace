# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
# Space adaptations are documented in ../README.md.

"""
Base Memory Retrieval Class

This module defines the abstract base class for memory retrieval strategies.
It provides a common interface for different retrieval approaches including
semantic search, hybrid search, and reinforcement learning-based retrieval.
"""

from abc import ABC, abstractmethod
from typing import Any
from typing import Any as DictConfig

from space._vendor.memora.core.memory_entry import MemoryEntry


class BaseMemoryRetriever(ABC):
    """
    Abstract base class for memory retrieval strategies.

    This class defines the interface for different memory retrieval approaches.
    Implementations can range from simple semantic search to sophisticated
    multi-step retrieval with reinforcement learning.

    Key responsibilities:
    - Execute retrieval queries against memory stores
    - Apply retrieval strategies (semantic, hybrid, RL-based, etc.)
    - Filter and rank retrieved memories
    - Manage retrieval configuration and hyperparameters

    Usage:
        class MyRetriever(BaseMemoryRetrieval):
            def retrieve(self, query: str, **kwargs) -> RetrievalResult:
                # Custom retrieval logic
                memories = self._fetch_memories(query)
                return RetrievalResult(
                    memories=memories,
                    query=query,
                    retrieval_time=elapsed_time,
                    strategy="my_strategy"
                )
    """

    def __init__(self, cfg: DictConfig):
        """
        Initialize the base memory retrieval.

        Args:
            cfg: Configuration object containing retrieval settings
            user_id: Optional user identifier for user-specific retrieval
        """
        self.cfg = cfg

    @abstractmethod
    def retrieve(
        self, query: str, top_k: int | None = None, filters: dict[str, Any] | None = None, **kwargs
    ) -> list[MemoryEntry]:
        """
        Retrieve memories based on the query.

        This is the main entry point for memory retrieval. Implementations
        should handle the complete retrieval pipeline including query processing,
        memory search, filtering, ranking, and result formatting.

        Args:
            query: Natural language query for retrieval
            top_k: Maximum number of memories to retrieve (overrides config)
            filters: Optional metadata filters to apply during retrieval
            **kwargs: Additional retrieval-specific parameters

        Returns:
            RetrievalResult containing retrieved memories and metadata
        """
        raise NotImplementedError
