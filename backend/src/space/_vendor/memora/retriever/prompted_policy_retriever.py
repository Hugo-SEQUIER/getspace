# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
# Space adaptations are documented in ../README.md.

"""
Semantic Memory Retrieval

This module provides a semantic search-based memory retrieval implementation
that extends the BaseMemoryRetrieval class.
"""

import json
import logging
import time
from typing import Any
from typing import Any as DictConfig

from space._vendor.memora.core.memory import AgentMemory, QueryMode
from space._vendor.memora.core.memory_entry import MemoryEntry
from space._vendor.memora.core.memory_expander import MemoryExpander
from space._vendor.memora.retriever.base_retriever import BaseMemoryRetriever
from space._vendor.memora.utils.llm import ChatCompletionModel
from space._vendor.memora.utils.memory import dedup_memories

logger = logging.getLogger(__name__)
LLM_POLICY_PROMPT = """
SYSTEM:
You are Memora-Control, a decision policy for an iterative memory system. Your goal is to help retrieve relevant memories to answer user questions.

Initial retrieval has ALREADY been performed, yielding a Working Set (W) of memories and a Frontier (F) of expansion candidates.
Your job is to decide how to proceed using ONLY these actions:
- EXPAND: grow memory from expansion candidates in Frontier if they add useful information to answering the user question.
- RE_QUERY: regenerate a better query and retrieve again, if current memories are insufficient to answer the question, or important facts are missing.
- STOP: enough information has been collected in the Working Set (W) to answer the user question.

Definitions:
- Working Set (W): memories already collected
- Frontier (F): expansion candidates reachable from W
- Expansion is cheaper than re-querying the full corpus

Decision rules:
1) Choose STOP if W is sufficient to answer the user question with high confidence.
2) Choose EXPAND if frontier contains high-novelty items likely to fill remaining gaps.
3) Choose RE_QUERY if important facts are missing AND expansion is unlikely to find them.
4) Here are some typical scenarios for RE_QUERY:
    (a) Query refinement: The previous query failed to return relevant results, or the question requires a different semantic angle.
    (b) Relative answers: If W provides a relative answer (a "pointer") rather than a direct value. You need to RE-QUERY for the specific target.
        - *Example:*
            - Query: "Where did Mike go to college?"
            - W: "Mike went to the same college as Sarah"  (relative answer, no specific college - gap identified)
            - Action: RE_QUERY -> `new_query`: "Where did Sarah go to college?" 
        - *Example:*
            - Query: "When is the deadline for Project X?"
            - W: "The deadline is three months after the project kickoff." (relative answer, no specific date - gap identified)
            - Action: RE_QUERY -> `new_query`: "When was the kickoff date for Project X?"
5) Prefer EXPAND over RE_QUERY when both are viable.
6) Minimize redundancy and unnecessary steps.

Output STRICT JSON. No extra text.

JSON schema:
{{
  'action': 'EXPAND | RE_QUERY | STOP',
  'reason': 'one sentence',
  'confidence': 0.0-1.0,
  'frontier_ids': ['id1','id2'],  // required if EXPAND, pick from frontier
  'new_query': 'string',          // required if RE_QUERY
}}

UserQuestion:
{user_question}

CurrentQuery:
{current_query}

Step:
{step}/{max_steps}

WorkingSetSummary:
{W_summary}

FrontierSummary:
{F_summary}

Running History:
{trace}

Constraints:
- Prefer EXPAND over RE_QUERY when possible
- Avoid repeating information already covered in W
- RE_QUERY should be concise and retrieval-optimized
- Avoid RE_QUERY with the same query as before
"""


class PromptedPolicyRetriever(BaseMemoryRetriever):
    """
    LLM-guided iterative memory retrieval using prompted policy.

    This class implements a multi-step retrieval approach where an LLM
    decides whether to expand the working set via frontier exploration,
    re-query the corpus with a refined query, or stop retrieval.

    This serves as a **baseline** for comparison with RL-trained retrieval policies.

    Features:
    - Initial semantic retrieval
    - Frontier expansion via memory links
    - LLM-guided action selection (EXPAND/RE_QUERY/STOP)
    - Configurable max steps

    Example:
        retriever = PromptedPolicyRetriever(cfg, memory_client=agent_memory)
        memories = retriever.retrieve("What project is Jolene working on?", top_k=10)
    """

    def __init__(
        self,
        cfg: DictConfig,
        memory_client: AgentMemory | None = None,
        model_client: ChatCompletionModel | None = None,
        max_steps: int = 5,
    ):
        """
        Initialize semantic retrieval.

        Args:
            cfg: Configuration object
            memory_client: Optional pre-initialized memory client
            model_client: Optional LLM client for policy decisions
            max_steps: Maximum retrieval iterations (default: 5)
        """
        super().__init__(cfg)
        self.memory_client = memory_client
        self.model_client = model_client or ChatCompletionModel(cfg)
        self.top_k = self.cfg.memory.get("top_k", 10)
        self.enable_hybrid_search = self.cfg.memory.get("enable_hybrid_search", False)
        self.enable_llm_filter = self.cfg.retrieval.get("enable_llm_filter", False)
        if self.cfg.memory.get("enable_cue_index", False):
            self.query_mode = QueryMode.BOTH
        else:
            self.query_mode = QueryMode.PRIMARY_ONLY
        enable_relaxed_frontier = self.cfg.retrieval.get("enable_relaxed_frontier", False)
        max_workers = self.cfg.eval.get("max_workers", 5)
        self.expander = MemoryExpander(
            memory_client=memory_client,
            enable_relaxed_frontier=enable_relaxed_frontier,
            max_workers=max_workers,
        )
        self.max_steps = max_steps
        self.last_trace = []

    def _select_from_frontier(
        self, frontier: dict[str, MemoryEntry], frontier_ids: list[str] = None
    ) -> list[MemoryEntry]:
        """
        Select memories from frontier to add to working set.
        Args:
            frontier: Current frontier dictionary
            frontier_ids: Specific IDs requested by LLM
            max_expand: Maximum items to expand if no specific IDs given

        Returns:
            List of MemoryEntry objects to add
        """
        selected = []
        if not frontier_ids:
            return selected
        for fid in frontier_ids:
            if fid in frontier:
                selected.append(frontier[fid])
        return selected

    def expand(self, memories: list[MemoryEntry]) -> list[MemoryEntry]:
        """
        Expand the given memory entries by following their links.

        Args:
            memories: List of MemoryEntry objects to expand

        Returns:
            Expanded list of MemoryEntry objects
        """
        expanded_memories = memories.copy()
        for memory in memories:
            if memory.links:
                linked_memories = self.memory_client.get_by_ids(memory.links)
                expanded_memories.extend(linked_memories)
        return expanded_memories

    def _format_working_set(self, memories: list[MemoryEntry]) -> str:
        """Format working set memories for the prompt."""
        if not memories:
            return "(empty)"
        lines = []
        for i, mem in enumerate(memories[:]):
            value = (mem.value or "")[:150]
            lines.append(
                f"[{i + 1}] {mem.index}: {value}{('...' if len(mem.value or '') > 150 else '')}"
            )
        return "\n".join(lines)

    def _format_frontier(self, frontier: dict[str, MemoryEntry]) -> str:
        """Format frontier candidates for the prompt."""
        if not frontier:
            return "(empty - no expansion candidates)"
        lines = []
        for mem_id, mem in frontier.items():
            value = (mem.value or "")[:100]
            lines.append(f"- [{mem_id}]: {value}{('...' if len(mem.value or '') > 100 else '')}")
        return "\n".join(lines)

    def prompted_policy(
        self,
        user_question: str,
        current_query: str,
        memory_entries: list[MemoryEntry],
        frontier: dict[str, MemoryEntry],
        step: int,
        trace: list | None = None,
        latency_tracker=None,
    ) -> dict[str, Any]:
        """
        Use an LLM to decide the next action based on the current state.

        Args:
            user_question: The original user question
            current_query: Current query being used
            memory_entries: List of memories in working set
            frontier: Dictionary of frontier candidates
            step: Current step number
            max_steps: Maximum number of steps allowed

        Returns:
            Decision dictionary with action and parameters
        """
        W_summary = self._format_working_set(memory_entries)
        F_summary = self._format_frontier(frontier)
        trace_str = "\n".join([str(t) for t in trace]) if trace else "No history yet"
        prompt = LLM_POLICY_PROMPT.format(
            user_question=user_question,
            current_query=current_query,
            step=step,
            max_steps=self.max_steps,
            W_summary=W_summary,
            F_summary=F_summary,
            trace=trace_str,
        )
        try:
            llm_start = time.time()
            response = self.model_client.invoke(
                input=prompt, temperature=0.0, source="PromptedPolicyRetriever"
            )
            llm_duration = time.time() - llm_start
            if isinstance(response, str):
                response = response.strip()
                if response.startswith("```json"):
                    response = response[7:]
                if response.startswith("```"):
                    response = response[3:]
                if response.endswith("```"):
                    response = response[:-3]
                response = response.strip()
                decision = json.loads(response)
            else:
                decision = response
            if "action" not in decision:
                decision["action"] = "STOP"
            decision["llm_duration"] = llm_duration
            if latency_tracker:
                latency_tracker.add_timing("policy_llm", llm_duration)
            return decision
        except Exception:
            raise RuntimeError("Memora processing failed") from None

    def retrieve(
        self,
        query: str,
        top_k: int | None = None,
        filters: dict[str, Any] | None = None,
        enhance_query: bool = False,
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
            filters: Metadata filters for retrieval
            enhance_query: Whether to use LLM query enhancement
            enable_hybrid_search: Whether to enable hybrid search
            enable_llm_filter: Whether to enable LLM-based filtering
            query_mode: Query mode (ORIGINAL, CUE, or BOTH)
            latency_tracker: Optional LatencyTracker for performance measurement
            **kwargs: Additional parameters

        Returns:
            RetrievalResult with retrieved memories
        """
        self.expander.reset()
        self.last_trace = []
        if top_k is None:
            top_k = self.top_k
        if enable_hybrid_search is None:
            enable_hybrid_search = self.enable_hybrid_search
        if enable_llm_filter is None:
            enable_llm_filter = self.enable_llm_filter
        if query_mode is None:
            query_mode = self.query_mode
        step_start = time.time()
        memory_entries = self.memory_client.query(
            query,
            top_k=top_k,
            enable_hybrid_search=enable_hybrid_search,
            enable_llm_filter=enable_llm_filter,
            query_mode=query_mode,
            latency_tracker=latency_tracker,
        )
        current_query = query
        frontier: dict[str, MemoryEntry] = {}
        frontier = self.expander.build_frontier(frontier, memory_entries)
        step_duration = time.time() - step_start
        step_data = {
            "step": 0,
            "action": "INIT_RETRIEVE",
            "query": query,
            "memories_count": len(memory_entries),
            "frontier_size": len(frontier),
            "duration": step_duration,
        }
        if latency_tracker:
            search_breakdown = latency_tracker.get_search_breakdown()
            if search_breakdown:
                step_data["search_components"] = search_breakdown
        self.last_trace.append(step_data)
        if latency_tracker:
            latency_tracker.add_retrieval_step(step_data)
        pass
        for step_idx in range(1, self.max_steps + 1):
            step_start = time.time()
            decision = self.prompted_policy(
                user_question=query,
                current_query=current_query,
                memory_entries=memory_entries,
                frontier=frontier,
                step=step_idx,
                trace=self.last_trace,
                latency_tracker=latency_tracker,
            )
            action = decision.get("action", "STOP")
            if action == "STOP":
                step_duration = time.time() - step_start
                step_data = {
                    "step": step_idx,
                    "action": "STOP",
                    "reason": decision.get("reason", ""),
                    "confidence": decision.get("confidence", 0.0),
                    "duration": step_duration,
                    "llm_duration": decision.get("llm_duration", 0.0),
                }
                self.last_trace.append(step_data)
                if latency_tracker:
                    latency_tracker.add_retrieval_step(step_data)
                pass
                break
            if action == "EXPAND":
                frontier_ids = decision.get("frontier_ids", [])
                chosen = self._select_from_frontier(frontier, frontier_ids)
                pass
                pass
                if chosen:
                    memory_entries = dedup_memories(memory_entries + chosen)
                    for mem in chosen:
                        frontier.pop(mem.index, None)
                    frontier = self.expander.build_frontier(frontier, chosen)
                step_duration = time.time() - step_start
                step_data = {
                    "step": step_idx,
                    "action": "EXPAND",
                    "added": len(chosen),
                    "frontier_ids": frontier_ids,
                    "new_frontier_size": len(frontier),
                    "duration": step_duration,
                    "llm_duration": decision.get("llm_duration", 0.0),
                }
                self.last_trace.append(step_data)
                if latency_tracker:
                    latency_tracker.add_retrieval_step(step_data)
                pass
                continue
            if action == "RE_QUERY":
                new_query = decision.get("new_query", current_query)
                current_query = new_query
                new_entries = self.memory_client.query(
                    current_query,
                    top_k=top_k,
                    enable_hybrid_search=enable_hybrid_search,
                    enable_llm_filter=enable_llm_filter,
                    query_mode=query_mode,
                    latency_tracker=latency_tracker,
                )
                memory_entries = dedup_memories(memory_entries + new_entries)
                frontier = self.expander.build_frontier(frontier, new_entries)
                step_duration = time.time() - step_start
                step_data = {
                    "step": step_idx,
                    "action": "RE_QUERY",
                    "new_query": new_query,
                    "new_memories": len(new_entries),
                    "total_memories": len(memory_entries),
                    "duration": step_duration,
                    "llm_duration": decision.get("llm_duration", 0.0),
                }
                if latency_tracker:
                    search_breakdown = latency_tracker.get_search_breakdown()
                    if search_breakdown:
                        step_data["search_components"] = search_breakdown
                self.last_trace.append(step_data)
                if latency_tracker:
                    latency_tracker.add_retrieval_step(step_data)
                pass
                continue
            step_duration = time.time() - step_start
            step_data = {
                "step": step_idx,
                "action": "INVALID",
                "decision": decision,
                "duration": step_duration,
            }
            self.last_trace.append(step_data)
            if latency_tracker:
                latency_tracker.add_retrieval_step(step_data)
            pass
            break
        pass
        return memory_entries

    def get_trace(self) -> list[dict]:
        """Return the trace from the last retrieval for analysis"""
        return self.last_trace
