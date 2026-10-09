# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
# Space adaptations are documented in ../README.md.

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any
from typing import Any as DictConfig

from pydantic import BaseModel, Field

from space._vendor.memora.core.cue_index_generator import CueIndexGenerator
from space._vendor.memora.core.memory import AgentMemory, QueryMode
from space._vendor.memora.core.memory_entry import MemoryEntry
from space._vendor.memora.utils.llm import BadRequestError, ChatCompletionModel
from space._vendor.memora.utils.memory import (
    MemoryUpdateDecision,
    generate_history,
    generate_metadata,
)
from space._vendor.memora.utils.misc import get_current_timestamp, normalize_content

logger = logging.getLogger(__name__)
PROMPT_BUILD_MEMORY = """
You are an expert fatual memory extraction assistant. Your goal is to extract factual memories from a conversation segment.

# TASK: 
Read the input conversation carefully, extract ALL factual memories that could be useful for future reference.

Produce each memory as a key-value pair in the following format:

MemIndex: memory index for retrieval
MemValue: memory value with all the details supported directly from the given text.

# GUIDELINES:
1. Content and Scope:
- Use only information explicitly mentioned in the context to create the factual memories.
- Make sure to capture ALL factual information that could be useful for future retrieval. When in doubt, create more factual memories rather than fewer. Capture more details rather than less.
- Do not include greetings, small talk, or filler in the memories.
- Be exhaustive when extracting factual memories from the conversation, do not miss any details, unless they are explicitly irrelevant and unlikely to be useful for future reference (like greetings or small talk).
- Split distinct facts into separate entries.
- Capture all details about people's identities, experiences, past or upcoming events, intentions, hobbies, preferences, states, beliefs, goals, or future plans that may be useful for answering later questions.
- Make sure to include time of events, location, or other contextual details in the MemValue if mentioned.
- If images are included in the conversation, consider the images as a part of the text. DO NOT create a memory entry solely to describe the image. Instead, extract useful facts from the image (e.g., objects, locations) and merge them naturally into relevant memories. For example, if a conversation contains an image showing a birthday cake with candles, include information about that image in the relevant memory about the birthday event, such as "MemIndex: Alice celebrated her birthday
MemValue: Alice celebrated her birthday with her friends at home, including a birthday cake with candles".
- If the conversation is between a user and an AI assistant, focus on the user's inputs and the overall context rather than the assistant's responses.

2. Format and Style:
- The MemIndex must be a short, human-readable phrase that is self-contained and unambiguous.
- Always include the specific context (e.g., domain, or entity) from the source text in the MemIndex to avoid vague terms. For example, instead of "Vacation", use "Alice's Japan Vacation". Instead of "Mike's plans", use "Mike's summer plans to visit Europe".
- Write MemValue as one or two full factual sentences, capturing all relevant details. 
    - Ensure wording is neutral and factual.
    - Use the original wordings from the conversation when possible.
    - Replace pronouns with specific names or entities to ensure clarity.
    - Handling times and dates in the MemValue: When dates and times are mentioned in the conversation, replace relative times (e.g., "yesterday", "next week", "last year") with absolute dates based on the timestamp of the conversation. For example, if the conversation timestamp is "16 June, 2023" and it mentions something happened "last year", convert it to "2022" in the MemValue.

Timestamp of conversation: {timestamp}

Input Conversation:
{content}

Output:
"""
PROMPT_MEMORY_UPDATE_DECISION = """
You are a memory management assistant. Given a new memory entry and similar existing entries, determine whether to update an existing entry or add a new one.

NEW MEMORY ENTRY:
Index: {new_index}
Value: {new_value}

EXISTING SIMILAR ENTRIES:
{candidates_info}

INSTRUCTIONS:
1. Analyze if the new entry should update any existing entry based on semantic similarity and content overlap
2. If update is needed, determine which candidate is best to update
3. Generate the updated value that combines relevant information from both entries
4. Decide if the memory index should be updated to better reflect the combined information
"""
PROMPT_CANDIDATE_FORMAT = """
Candidate {index}:
- Similarity Score: {score:.3f}
- Index: {index_text}
- Value: {value}
- Creation Time: {creation_time}
"""


class MemoryOutput(BaseModel):
    memory_type: str = Field(description="Type of memory: 'Factual' or 'Procedural'.")
    index: str = Field(
        min_length=1,
        max_length=300,
        pattern=r"^[^|\r\n]+$",
        description="Use a short, specific phrase that captures the fact clearly, with enough detail to support retrieval.",
    )
    value: str = Field(
        min_length=1,
        max_length=2000,
        description="A concise but complete factual statement, supported directly by the conversation.",
    )

    def __str__(self) -> str:
        return f"[*{self.index}*] {self.value}"

    def __repr__(self) -> str:
        return str(self)


class MemoryOutputs(BaseModel):
    entries: list[MemoryOutput] = Field(
        max_length=20,
        description="memories extracted from the content that are factual and verifiable.",
    )


class MemoryBuilder(ABC):
    """All builders accept either text or file_path (exactly one required)."""

    def __init__(
        self, cfg: DictConfig, agent_memory: AgentMemory, model_client: ChatCompletionModel
    ):
        self.cfg = cfg
        self._model_client = model_client
        self.agent_memory = agent_memory
        self.multimodal_support = cfg.memory.get("multimodal_support", True)
        self.cue_index_generator = CueIndexGenerator(cfg=self.cfg, model_client=self._model_client)
        self.UPDATE_SCORE_THRESHOLD = cfg.memory.update_score_threshold
        self._build_stats = {}

    def build(
        self,
        content: str | list[str] | list[dict[str, str]],
        metadata: dict | None = None,
        progress_callback: Callable[[int, int, str], None] | None = None,
    ) -> list[MemoryEntry]:
        """
        Add multiple memory entries built from the provided context with intelligent deduplication.

        This method implements sophisticated memory management:
        1. Extracts structured memory entries from natural language context
        2. Checks for similar existing memories using semantic similarity
        3. Uses LLM to decide whether to update existing memories or create new ones
        4. Maintains history of updates for traceability
        5. Logs all operations for monitoring and debugging

        The method prevents duplicate memories by:
        - Comparing new entries against existing ones using vector similarity
        - Using configurable thresholds to determine update candidates
        - Leveraging LLM reasoning to make intelligent merge decisions
        - Maintaining update history for transparency

        Args:
            context: Context to build memory from. Flexible input types:
                - str: Natural language text to extract memories from
                - List[str]: Multiple text entries to process
                - List[Dict[str, str]]: Structured context with key-value pairs
            metadata: Additional metadata to store with memories (user_id, tags, etc.)
        Returns:
            MemoryEntries: Object containing all extracted memory entries

        Note:
            Uses timestamp-based filtering to avoid updating memories from the same batch
        """
        "\n        Progress callback signature: (current_step, total_steps, message)\n        \n        Weighted progress:\n        - Step 0-40: Extracting memories (40 percent given as LLM extraction is heavy)\n        - Step 40-95: Processing individual memories (varies by count)\n        - Step 95-100: Finalization\n        "
        self._build_stats = {"new": 0, "update": 0, "duplicate": 0, "extracted": 0}
        if progress_callback:
            progress_callback(0, 100, "Extracting memories from text")
        content = normalize_content(content, multimodal_support=self.multimodal_support)
        pass
        metadata = generate_metadata(content, metadata)
        enable_episodic = self.cfg.memory.get("enable_episodic_memory", False)
        episodic_entry = None
        if enable_episodic:
            segment_as_ep_mem = self.cfg.memory.get("use_segments_as_episodic", False)
            episodic_entry = self.generate_episodic_memory(content, metadata, segment_as_ep_mem)
            if episodic_entry:
                self.agent_memory.add(episodic_entry)
                metadata["episodic_memory_id"] = episodic_entry.index
        memory_entries = self.generate_memory_entries(
            content, metadata, enable_cue_index=self.cfg.memory.enable_cue_index
        )
        self._build_stats["extracted"] = len(memory_entries)
        if progress_callback:
            pass
            progress_callback(40, 100, f"Processing {len(memory_entries)} memories...")
        total_entries = len(memory_entries)
        for idx, entry in enumerate(memory_entries):
            if progress_callback:
                if total_entries > 0:
                    progress_in_phase = int(40 + 55 * (idx + 1) / total_entries)
                    progress_callback(
                        progress_in_phase, 100, f"Processing memory {idx + 1}/{total_entries}"
                    )
            self.upsert_memory_entry(entry=entry)
        if progress_callback:
            progress_callback(100, 100, "Memories saved successfully!")
        return memory_entries

    @abstractmethod
    def generate_memory_entries(
        self, content: str, metadata: dict | None, enable_cue_index: bool = False
    ) -> list[MemoryEntry]:
        """
        Abstract method to generate memory entries from content.
        Must be implemented by subclasses.
        """
        ...

    @abstractmethod
    def generate_episodic_memory(self, content: str, metadata: dict | None) -> MemoryEntry | None:
        """
        Abstract method to generate episodic memory from content.
        Must be implemented by subclasses.
        Returns None if episodic memory generation is not supported or fails.
        """
        ...

    def handle_multimodal_content(
        self,
        content: str | dict | None,
        metadata: dict | None,
        build_memory_prompt: str,
        response_format: Any,
    ) -> MemoryOutputs | None:
        if not isinstance(content, dict) or not self.multimodal_support or "image" not in content:
            return None
        formatted_prompt = build_memory_prompt.format(
            content=content["text"], timestamp=metadata.get("timestamp", "") if metadata else ""
        )
        messages = [
            {
                "role": "user",
                "content": [{"type": "text", "text": formatted_prompt}] + content["image"],
            }
        ]
        try:
            memories = self._model_client.invoke(input=messages, response_format=response_format)
        except BadRequestError:
            raise RuntimeError("Memora processing failed") from None
        return memories

    def _query_update_candidates(self, entry: MemoryEntry) -> list[MemoryEntry]:
        """
        Query existing memories to find candidates for update consideration.
        Returns:
            List of MemoryEntry objects that are candidates for update
        """
        where = {
            "$and": [
                {"creation_time": {"$ne": entry.creation_time}},
                {"linked_memory": {"$eq": ""}},
                {"memory_type": {"$eq": "factual"}},
            ]
        }
        query_results: list[MemoryEntry] = self.agent_memory.query(
            entry.index,
            top_k=5,
            where=where,
            query_mode=QueryMode.PRIMARY_ONLY,
            enhance_query=False,
            return_history=False,
        )
        update_candidates = []
        for cand in query_results:
            score = cand.score
            if score >= self.UPDATE_SCORE_THRESHOLD:
                update_candidates.append(cand)
        return update_candidates

    def upsert_memory_entry(self, entry: MemoryEntry) -> None:
        """
        Process a single memory entry with intelligent deduplication.

        This method handles the complex logic of:
        1. Checking for similar existing memories
        2. Making update decisions using LLM
        3. Managing memory history and metadata
        4. Logging operations

        Args:
            entry: Memory entry to process
            memory_store: The memory storage backend
            metadata: Metadata for the memory entry
            update_score_threshold: Threshold for considering updates
            query_method: Method to query existing memories
        """
        existing_entry = self.agent_memory.get(entry.index)
        if existing_entry:
            if existing_entry.value == entry.value:
                self._build_stats["duplicate"] += 1
                return
            decision = self._decide_memory_update(entry, [existing_entry])
            if decision["should_update"]:
                self.update_memory(entry, decision)
                self._build_stats["update"] += 1
                return
            suffix = 2
            base = entry.index
            while self.agent_memory.get(f"{base} ({suffix})"):
                suffix += 1
            entry.index = f"{base} ({suffix})"
        update_candidates = self._query_update_candidates(entry)
        if not update_candidates:
            self.agent_memory.add(entry)
            self._build_stats["new"] += 1
        else:
            update_decision = self._decide_memory_update(entry, update_candidates)
            if update_decision["should_update"]:
                self.update_memory(entry, update_decision)
                self._build_stats["update"] += 1
            else:
                self.agent_memory.add(entry)
                self._build_stats["new"] += 1

    def merge_memory(self, existing_entry: MemoryEntry, new_entry: MemoryEntry) -> MemoryEntry:
        """
        Merge two memory entries into one.

        Args:
            existing_entry: Existing memory entry
            new_entry: New memory entry to merge

        Returns:
            Merged MemoryEntry
        """
        if existing_entry.memory_type != new_entry.memory_type:
            pass
            raise ValueError("Cannot merge memories of different types")
        merged_value = f"{existing_entry.value} {new_entry.value}"
        merged_cue_indices = list(
            set(existing_entry.cue_indices.split("||") + new_entry.cue_indices.split("||"))
        )
        merged_episodic_ids = []
        if existing_entry.episodic_memory_ids:
            merged_episodic_ids.extend(existing_entry.episodic_memory_ids)
        if new_entry.episodic_memory_ids:
            merged_episodic_ids.extend(new_entry.episodic_memory_ids)
        merged_episodic_ids = list(set(merged_episodic_ids))
        merged_image_urls = []
        if existing_entry.image_urls:
            merged_image_urls.extend(existing_entry.image_urls)
        if new_entry.image_urls:
            merged_image_urls.extend(new_entry.image_urls)
        merged_image_urls = list(set(merged_image_urls))
        merged_entry = MemoryEntry(
            memory_type=existing_entry.memory_type,
            index=existing_entry.index,
            value=merged_value,
            user_id=existing_entry.user_id,
            creation_time=existing_entry.creation_time,
            timestamp=new_entry.timestamp,
            cue_indices="||".join(merged_cue_indices),
            episodic_memory_ids=merged_episodic_ids,
            image_urls=merged_image_urls,
        )
        return merged_entry

    def update_memory(self, entry: MemoryEntry, update_decision: dict[str, Any]) -> None:
        """
        Handle the memory update process including history management.

        Args:
            entry: New memory entry
            update_decision: LLM decision about the update
            memory_store: The memory storage backend
            metadata: Metadata for the memory entry
            timestamp: Current timestamp
            creation_time: Creation time for the batch
        """
        best_candidate: MemoryEntry = update_decision["best_candidate"]
        if best_candidate.memory_type != "factual":
            pass
            self.agent_memory.add(entry)
            return
        updated_value = update_decision["updated_value"]
        updated_index = update_decision["updated_index"]
        existing_entry = self.agent_memory.get(updated_index)
        if existing_entry and updated_index != best_candidate.index:
            updated_index = f"{updated_index}. (Added on {get_current_timestamp()}]"
        self.agent_memory.delete(best_candidate.index)
        updated_cue_indices = []
        if self.cfg.memory.enable_cue_index:
            updated_cue_indices = self.cue_index_generator.generate_cue_indices(
                memory_value=updated_value, primary_index=updated_index
            )
        history = generate_history(entry, best_candidate)
        updated_image_urls = []
        if best_candidate.image_urls:
            updated_image_urls.extend(best_candidate.image_urls)
        if entry.image_urls:
            updated_image_urls.extend(entry.image_urls)
        updated_image_urls = list(set(updated_image_urls))
        updated_episodic_memory_ids = []
        if best_candidate.episodic_memory_ids:
            updated_episodic_memory_ids.extend(best_candidate.episodic_memory_ids)
        if entry.episodic_memory_ids:
            updated_episodic_memory_ids.extend(entry.episodic_memory_ids)
        updated_episodic_memory_ids = list(set(updated_episodic_memory_ids))
        new_memory_entry = MemoryEntry(
            memory_type=best_candidate.memory_type,
            index=updated_index,
            value=updated_value,
            creation_time=entry.creation_time,
            timestamp=entry.timestamp,
            cue_indices="||".join(updated_cue_indices),
            history=history,
            image_urls=updated_image_urls,
            episodic_memory_ids=updated_episodic_memory_ids,
        )
        self.agent_memory.add(new_memory_entry)
        pass

    def _decide_memory_update(self, new_entry, update_candidates) -> dict[str, Any]:
        """
        Use LLM to make intelligent decisions about memory updates vs new additions.

        Args:
            new_entry: New memory entry being added
            update_candidates: List of similar existing memory entries

        Returns:
            Dict containing the LLM's decision
        """
        candidates_info = []
        for i, candidate in enumerate(update_candidates):
            candidates_info.append(
                {
                    "index": i,
                    "score": candidate.score,
                    "index_text": candidate.index,
                    "value": candidate.value,
                    "creation_time": candidate.creation_time,
                }
            )
        prompt_args = {
            "new_index": new_entry.index,
            "new_value": new_entry.value,
            "candidates_info": self._format_candidates_for_prompt(candidates_info),
        }
        try:
            decision: MemoryUpdateDecision = self._model_client.invoke(
                input=PROMPT_MEMORY_UPDATE_DECISION,
                prompt_args=prompt_args,
                response_format=MemoryUpdateDecision,
            )
            decision_dict = decision.model_dump()
            if decision.should_update and decision.best_candidate_index is not None:
                decision_dict["best_candidate"] = update_candidates[decision.best_candidate_index]
            else:
                decision_dict["best_candidate"] = None
            return decision_dict
        except Exception:
            raise RuntimeError("Memora processing failed") from None

    def _format_candidates_for_prompt(self, candidates_info: list[dict]) -> str:
        """
        Format candidate memory information for inclusion in LLM prompts.

        Args:
            candidates_info: List of dictionaries containing candidate information

        Returns:
            str: Formatted string representation of all candidates
        """
        formatted = []
        for candidate in candidates_info:
            formatted.append(
                PROMPT_CANDIDATE_FORMAT.format(
                    index=candidate["index"],
                    score=candidate["score"],
                    index_text=candidate["index_text"],
                    value=candidate["value"],
                    creation_time=candidate["creation_time"],
                )
            )
        return "\n".join(formatted)
