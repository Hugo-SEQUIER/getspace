# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
# Space adaptations are documented in ../README.md.

"""
Agent Memory Facade - Provides a unified interface for different memory storage backends.
"""

import enum
import logging
from typing import Any as DictConfig
from typing import Any as Where

from space._vendor.memora.core.base import MemoryBase
from space._vendor.memora.core.memory_entry import MemoryEntry
from space._vendor.memora.core.memory_filter import MemoryFilter
from space._vendor.memora.core.query_generator import QueryGenerator
from space._vendor.memora.utils.llm import ChatCompletionModel
from space._vendor.memora.utils.memory import combine_list
from space._vendor.memora.utils.misc import context_to_str, extract_user_id_from_where, index_to_id

logger = logging.getLogger(__name__)


class QueryMode(enum.Enum):
    ORIGINAL = 1
    PRIMARY_ONLY = 2
    CUE_ONLY = 3
    BOTH = 4


class AgentMemory(MemoryBase):
    """
    Facade class that provides a unified interface for different memory storage backends.

    This class abstracts the choice between local persistent storage and HTTP-based storage,
    allowing users to switch between backends via configuration without changing their code.

    Supported backends:
    - 'local': Local persistent storage using ChromaDB
    - 'http': Remote storage via HTTP API calls
    """

    def __init__(self, cfg: DictConfig, user_id: str):
        """
        Initialize the memory facade with the specified backend and components.

        This constructor sets up all necessary components for memory operations:
        - Storage backend (local ChromaDB or HTTP)
        - Query generator for enhanced search capabilities
        - Memory builder for extracting memories and handling update logic
        - Similarity thresholds for intelligent memory management

        Args:
            cfg: Configuration object containing memory settings including:
                - memory.backend: Storage backend type ('local' or 'http')
                - memory.query_score_threshold: Minimum similarity for query results
                - memory.update_score_threshold: Minimum similarity for update consideration
                - memory.multimodal_support: Whether to enable multimodal processing (default: True)
                - Additional backend-specific configuration

        Raises:
            ValueError: If unsupported backend type is specified
        """
        self.cfg = cfg
        self.user_id = user_id
        self.MAX_CONTEXT_TOKENS = 128000
        self.query_generator = QueryGenerator(cfg)
        self.memory_filter = MemoryFilter(cfg)
        self.multimodal_support = cfg.memory.get("multimodal_support", True)
        self._llm_client = ChatCompletionModel(cfg)
        self._store = cfg.space_store
        self.QUERY_SCORE_THRESHOLD = cfg.memory.query_score_threshold

    def get_user_id(self) -> str:
        """
        Get the user ID associated with this memory facade.

        Returns:
            str: User ID
        """
        return self.user_id

    def _query_result(
        self,
        queries: list[str],
        top_k: int,
        where: Where | None = None,
        query_mode: QueryMode = QueryMode.ORIGINAL,
        include: list[str] | None = None,
        return_history: bool = False,
    ):
        memory_results = []
        extracted = set()
        factual_memory_condition = {"memory_type": {"$in": ["factual", "procedural"]}}
        if query_mode == QueryMode.CUE_ONLY:
            where = (
                {"$and": [where, {"linked_memory": {"$ne": ""}}]}
                if where
                else {"linked_memory": {"$ne": ""}}
            )
        elif query_mode == QueryMode.PRIMARY_ONLY:
            query_condition = {"linked_memory": {"$eq": ""}}
            where = (
                {"$and": [query_condition, factual_memory_condition, where]}
                if where
                else {"$and": [query_condition, factual_memory_condition]}
            )
        for query in queries:
            results: list[MemoryEntry] = self._store.query(query, top_k, where, include)
            scores = [entry.score for entry in results]
            merged = list(zip(results, scores, strict=False))
            merged_sorted = sorted(merged, key=lambda x: x[1], reverse=True)
            for entry, score in merged_sorted:
                if score < self.QUERY_SCORE_THRESHOLD:
                    break
                if entry.is_cue_index():
                    for primary_index in entry.get_linked_memories():
                        primary_entry = self._store.get(primary_index)
                        if not primary_entry:
                            pass
                            continue
                        if primary_entry.index in extracted:
                            continue
                        memory_results.append(primary_entry)
                        extracted.add(primary_entry.index)
                else:
                    if entry.index in extracted:
                        continue
                    memory_results.append(entry)
                    extracted.add(entry.index)
        return memory_results[:top_k]

    def _perform_hybrid_search(self, context: str, where: Where | None = None) -> list[MemoryEntry]:
        """
        Perform hybrid search (keyword or BM25) based on configuration.

        Args:
            context: Search query string
            where: Filter conditions for metadata-based filtering

        Returns:
            List of MemoryEntry objects from hybrid search
        """
        hybrid_method = self.cfg.memory.get("hybrid_search_method", "bm25")
        hybrid_top_k = self.cfg.memory.get("hybrid_top_k", 10)
        if hybrid_method == "bm25":
            target_user_id = extract_user_id_from_where(where) or self.user_id
            if target_user_id and target_user_id not in self._store._bm25_indices:
                pass
                self._store.build_bm25_index(user_id=target_user_id)
        hybrid_results = []
        if hybrid_method == "bm25":
            bm25_threshold = self.cfg.memory.get("bm25_score_threshold", 0.4)
            hybrid_results = self._store.bm25_search(context, hybrid_top_k, where, bm25_threshold)
        elif hybrid_method == "keyword":
            keywords = self.query_generator.extract_keywords(context)
            if keywords:
                hybrid_results = self._store.keyword_search(keywords, hybrid_top_k, where)
        else:
            raise ValueError(f"Unsupported hybrid search method: {hybrid_method}")
        return hybrid_results

    def _merge_results_with_rrf(
        self, result_lists: list[list[MemoryEntry]], weights: list[float] | None = None, k: int = 60
    ) -> list[MemoryEntry]:
        """
        Merge multiple search result lists using weighted Reciprocal Rank Fusion (RRF).

        RRF combines ranked lists without relying on raw scores, using position-based scoring:
        RRF_score(d) = sum(weight_i * (1 / (k + rank_i(d)))) for all lists where d appears

        Args:
            result_lists: List of result lists from different search sources
            weights: List of weights for each result list (default: [2.0, 1.0, 1.0] for [primary, cue, hybrid])
            k: RRF constant (default 60) to prevent division by zero and reduce top-rank impact

        Returns:
            Combined and re-ranked list of MemoryEntry objects with weighted RRF scores
        """
        if weights is None:
            weights = [2.0] * len(result_lists)
        if len(weights) != len(result_lists):
            raise ValueError(
                f"Number of weights ({len(weights)}) must match number of result lists ({len(result_lists)})"
            )
        rrf_scores = {}
        all_entries = {}
        for result_list, weight in zip(result_lists, weights, strict=False):
            for rank, entry in enumerate(result_list, start=1):
                record_id = index_to_id(entry.index)
                if record_id not in all_entries:
                    all_entries[record_id] = entry
                if record_id in rrf_scores:
                    rrf_scores[record_id] += weight * (1.0 / (k + rank))
                else:
                    rrf_scores[record_id] = weight * (1.0 / (k + rank))
        if rrf_scores:
            max_score = max(rrf_scores.values())
            min_score = min(rrf_scores.values())
            score_range = max_score - min_score
            if score_range > 0:
                rrf_scores = {
                    record_id: (score - min_score) / score_range
                    for record_id, score in rrf_scores.items()
                }
            else:
                rrf_scores = {record_id: 1.0 for record_id in rrf_scores}
        for record_id, rrf_score in rrf_scores.items():
            if record_id in all_entries:
                all_entries[record_id].score = rrf_score
        return sorted(all_entries.values(), key=lambda x: x.score, reverse=True)

    def query(
        self,
        context: str | list[str] | list[dict[str, str]],
        top_k: int = 5,
        where: Where | None = None,
        query_mode: QueryMode = QueryMode.ORIGINAL,
        include: list[str] | None = None,
        enhance_query: bool = True,
        return_history: bool = False,
        enable_hybrid_search: bool = False,
        enable_llm_filter: bool = False,
        latency_tracker=None,
    ):
        """
        Perform intelligent semantic search to find relevant memories.

        This method implements sophisticated memory retrieval using:
        - Vector similarity search for semantic matching
        - Optional keyword-based search for exact term matching (hybrid search)
        - Optional LLM-powered query enhancement for better results
        - Similarity threshold filtering to ensure relevance
        - Deduplication to avoid returning the same memory multiple times
        - Metadata-based filtering for user/context-specific results

        The query process:
        1. Convert input context to standardized string format
        2. Optionally enhance query using LLM to generate multiple search variants
        3. If hybrid search enabled, extract keywords for keyword-based search
        4. Execute vector search and/or keyword search against the memory store
        5. Merge and deduplicate results from both search methods
        6. Filter results by similarity threshold
        7. Return ranked, relevant memories with metadata

        Args:
            context: Search context in flexible formats:
                - str: Natural language query text
                - List[str]: Multiple query strings to search
                - List[Dict[str, str]]: Structured context with key-value pairs
            top_k: Maximum number of results to return per query
            where: ChromaDB filter conditions for metadata-based filtering
                Example: {"user_id": "user-1", "timestamp": {"$gt": "2023-01-01"}}
            include: Fields to include in results ["metadatas", "distances", "documents"]
            filtering: Whether to apply similarity threshold filtering
            enhance_query: Whether to use LLM to generate enhanced query variants
            return_history: Whether to include memory update history in results
            enable_hybrid_search: Whether to enable hybrid search combining semantic and keyword search
            enable_llm_filter: Whether to use LLM to filter irrelevant memories
            query_mode: decide if we want to search with cue index (ORIGINAL, PRIMARY_ONLY, CUE_ONLY, BOTH)
            latency_tracker: Optional LatencyTracker for performance measurement

        Returns:
            List[Dict]: Ranked list of relevant memories, each containing:
                - memory: The memory content/value
                - metadata: Associated metadata (query, index, timestamp, etc.)
                - score: Similarity score (0-1, higher = more similar)

        Note:
            Uses deduplication to prevent the same memory from appearing multiple times
            when enhanced queries overlap in their results or when combining semantic
            and keyword search results.
        """
        context = context_to_str(context)
        memory_results = []
        if enhance_query:
            queries = self.query_generator.generate_queries(context)
        else:
            queries = [context]
        primary_results = []
        cue_results = []
        hybrid_results = []
        if query_mode == QueryMode.ORIGINAL:
            if latency_tracker:
                with latency_tracker.track("search_primary"):
                    primary_results = self._query_result(
                        queries, top_k, where, QueryMode.ORIGINAL, include, return_history
                    )
            else:
                primary_results = self._query_result(
                    queries, top_k, where, QueryMode.ORIGINAL, include, return_history
                )
            memory_results = primary_results
        elif query_mode == QueryMode.PRIMARY_ONLY:
            if latency_tracker:
                with latency_tracker.track("search_primary"):
                    primary_results = self._query_result(
                        queries, top_k, where, QueryMode.PRIMARY_ONLY, include, return_history
                    )
            else:
                primary_results = self._query_result(
                    queries, top_k, where, QueryMode.PRIMARY_ONLY, include, return_history
                )
            memory_results = primary_results
        elif query_mode == QueryMode.CUE_ONLY:
            if latency_tracker:
                with latency_tracker.track("search_cue"):
                    cue_results = self._query_result(
                        queries,
                        self.cfg.memory.cue_top_k,
                        where,
                        QueryMode.CUE_ONLY,
                        include,
                        return_history,
                    )
            else:
                cue_results = self._query_result(
                    queries,
                    self.cfg.memory.cue_top_k,
                    where,
                    QueryMode.CUE_ONLY,
                    include,
                    return_history,
                )
            memory_results = cue_results
        elif query_mode == QueryMode.BOTH:
            if latency_tracker:
                with latency_tracker.track("search_primary"):
                    primary_results = self._query_result(
                        queries, top_k, where, QueryMode.PRIMARY_ONLY, include, return_history
                    )
                with latency_tracker.track("search_cue"):
                    cue_results = self._query_result(
                        queries,
                        self.cfg.memory.cue_top_k,
                        where,
                        QueryMode.CUE_ONLY,
                        include,
                        return_history,
                    )
            else:
                primary_results = self._query_result(
                    queries, top_k, where, QueryMode.PRIMARY_ONLY, include, return_history
                )
                cue_results = self._query_result(
                    queries,
                    self.cfg.memory.cue_top_k,
                    where,
                    QueryMode.CUE_ONLY,
                    include,
                    return_history,
                )
        if enable_hybrid_search:
            try:
                if latency_tracker:
                    with latency_tracker.track("search_hybrid"):
                        hybrid_results = self._perform_hybrid_search(context, where)
                else:
                    hybrid_results = self._perform_hybrid_search(context, where)
                result_lists = []
                weights = []
                if primary_results:
                    result_lists.append(primary_results)
                    weights.append(2.0)
                if cue_results:
                    result_lists.append(cue_results)
                    weights.append(1.0)
                if hybrid_results:
                    result_lists.append(hybrid_results)
                    weights.append(1.0)
                if len(result_lists) > 1:
                    if latency_tracker:
                        with latency_tracker.track("search_rrf_merge"):
                            memory_results = self._merge_results_with_rrf(result_lists, weights)
                    else:
                        memory_results = self._merge_results_with_rrf(result_lists, weights)
                elif len(result_lists) == 1:
                    memory_results = result_lists[0]
                else:
                    memory_results = []
            except Exception:
                raise RuntimeError("Memora processing failed") from None
        elif query_mode == QueryMode.BOTH and primary_results and cue_results:
            if latency_tracker:
                with latency_tracker.track("search_rrf_merge"):
                    memory_results = self._merge_results_with_rrf(
                        [primary_results, cue_results], [2.0, 1.0]
                    )
            else:
                memory_results = self._merge_results_with_rrf(
                    [primary_results, cue_results], [2.0, 1.0]
                )
        if enable_llm_filter and memory_results:
            if latency_tracker:
                with latency_tracker.track("search_llm_filter"):
                    memory_results = self.memory_filter.filter_memory(
                        query=context, memory_results=memory_results
                    )
            else:
                memory_results = self.memory_filter.filter_memory(
                    query=context, memory_results=memory_results
                )
            return memory_results
        return memory_results[:top_k]

    def get_episodic_memories_for_results(
        self, memory_results: list[MemoryEntry]
    ) -> dict[str, MemoryEntry]:
        """
        Retrieve episodic memories linked to the given factual memory results.

        This method:
        1. Extracts all episodic_memory_ids from the factual memories
        2. Deduplicates them (since multiple factual memories may share the same episode)
        3. Fetches the actual episodic memory entries

        Args:
            memory_results: List of factual memory entries from query results

        Returns:
            Dict mapping episodic_memory_id to MemoryEntry for all linked episodes
        """
        episodic_ids = set()
        for entry in memory_results:
            if entry.episodic_memory_ids:
                episodic_ids.update(entry.episodic_memory_ids)
        episodic_memories = {}
        for episodic_id in episodic_ids:
            episodic_entry = self._store.get(episodic_id)
            if episodic_entry:
                episodic_memories[episodic_id] = episodic_entry
            else:
                pass
        return episodic_memories

    def get(self, key: str) -> MemoryEntry:
        """
        Retrieve a single record by its natural-language key.

        Args:
            key: Natural language key to retrieve

        Returns:
            Dict with id, metadata, document fields or None if not found
        """
        return self._store.get(key)

    def add(self, entry: MemoryEntry):
        """
        Add a single memory entry to the store.

        Args:
            entry: MemoryEntry object to add

        Returns:
            Record ID of the added memory entry
        """
        if not entry.is_primary_index():
            raise ValueError("Invalid Memora state")
        exist_entry = self._store.get(entry.index)
        if exist_entry is not None:
            if entry.memory_type == "episodic":
                original_index = entry.index
                counter = 2
                while self._store.get(f"{original_index} ({counter})") is not None:
                    counter += 1
                entry.index = f"{original_index} ({counter})"
                pass
            else:
                raise AssertionError(f"Memory entry {entry.index} already exists.")
        pass
        self._store.upsert(index=entry.index, value=entry.value, metadata=entry.get_metadata())
        for cue_index in entry.get_cue_indices():
            cue_entry = self._store.get(cue_index)
            if cue_entry and cue_entry.is_primary_index() or cue_index == entry.index:
                entry.delete_cue_index(cue_index)
                continue
            linked_memory = entry.index
            if cue_entry and cue_entry.is_cue_index():
                linked_memory = combine_list(linked_memory, cue_entry.linked_memory)
            self._store.upsert(index=cue_index, value="", metadata={"linked_memory": linked_memory})

    def _delete_cue_index(self, entry: MemoryEntry) -> None:
        """
        Delete a cue index and update linked primary memories.

        Args:
            key: Cue index key to delete
        """
        linked_memories = entry.get_linked_memories()
        for primary_index in linked_memories:
            primary_entry = self._store.get(primary_index)
            if not primary_entry is not None:
                raise ValueError("Invalid Memora state")
            primary_entry.cue_indices = "||".join(
                [ci for ci in primary_entry.get_cue_indices() if ci != entry.index]
            )
            self._store.upsert(
                index=primary_entry.index,
                value=primary_entry.value,
                metadata=primary_entry.get_metadata(),
            )
        self._store.delete(entry.index)

    def _delete_primary_memory(self, entry: MemoryEntry) -> None:
        """
        Delete a primary memory and all associated cue indices.

        Args:
            key: Primary memory key to delete
        """
        cue_indices = entry.get_cue_indices()
        for cue_index in cue_indices:
            cue_entry = self._store.get(cue_index)
            if cue_entry is None:
                raise AssertionError(
                    f"Cue entry '{cue_index}' not found. This may indicate a data consistency issue."
                )
            if cue_entry.is_cue_index():
                pass
            elif cue_entry.is_primary_index():
                pass
                continue
            else:
                raise AssertionError(
                    f"Cue entry '{cue_index}' is in an invalid state: not a cue index (linked_memory='{cue_entry.linked_memory}') and not a primary index. This requires investigation."
                )
            linked_memories = cue_entry.get_linked_memories()
            linked_memories = [lm for lm in linked_memories if lm != entry.index]
            if linked_memories:
                self._store.upsert(
                    index=cue_index,
                    value="",
                    metadata={"linked_memory": "||".join(linked_memories)},
                )
            else:
                self._store.delete(cue_index)
        self._store.delete(entry.index)

    def delete(self, key: str) -> None:
        """
        Delete a record by its natural-language key.

        Args:
            key: Natural language key to delete
        """
        entry = self._store.get(key)
        if entry.is_cue_index():
            self._delete_cue_index(entry)
        elif entry.is_primary_index():
            self._delete_primary_memory(entry)

    def list_memories(self, limit: int = 20) -> list[MemoryEntry]:
        """
        List memories in the collection.

        Args:
            limit: Max number of records to return

        Returns:
            Dict containing memory records
        """
        return self._store.list_memories(limit)

    def count(self) -> int:
        """
        Get the number of records in the collection.

        Returns:
            Number of records in the collection
        """
        return self._store.count()

    def get_backend_type(self) -> str:
        """
        Get the current backend type being used.

        Returns:
            Backend type ('local' or 'http')
        """
        return self.backend_type

    def clear(self) -> None:
        """
        Clear all records in the collection.
        """
        self._store.clear()
