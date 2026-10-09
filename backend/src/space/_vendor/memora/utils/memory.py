# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
# Space adaptations are documented in ../README.md.

from typing import Any

from pydantic import BaseModel, Field

from space._vendor.memora.core.base import MemoryStoreBase
from space._vendor.memora.core.memory_entry import MemoryEntry
from space._vendor.memora.utils.misc import get_current_timestamp


class MemoryUpdateDecision(BaseModel):
    """
    Pydantic model for structured LLM responses regarding memory update decisions.

    This model ensures type safety and validation for LLM responses when determining
    whether to update existing memory entries or create new ones. It replaces manual
    JSON parsing with structured response formats.

    Attributes:
        should_update: Boolean indicating if an existing memory should be updated
        best_candidate_index: Index of the best candidate for updating (if applicable)
        updated_value: New combined value for the memory entry (if updating)
        updated_index: Updated index/key for the memory entry (if changed)
    """

    should_update: bool = Field(description="Whether an existing memory entry should be updated")
    best_candidate_index: int | None = Field(
        description="Index of the best candidate to update if should_update is True", default=None
    )
    updated_value: str | None = Field(
        description="Combined value if updating an existing entry", default=None
    )
    updated_index: str | None = Field(
        description="Updated index if needed, or original index", default=None
    )
    updated_cues: list[str] = Field(
        description="Updated list of cue indices derived from the entities or the memory value to improve retrieval.             Don't use single word and the cue index should help the retrieval purpose",
        default=[],
    )


def combine_list(list_string1: str, list_string2: str, delimiter: str = "||") -> str:
    """
    Combine two string-based lists using a delimiter.

    This function merges two lists represented as delimited strings, removing duplicates
    and empty values while preserving order.

    Args:
        list_string1: First list as delimited string (can be empty or contain delimiter-separated values)
        list_string2: Second list as delimited string (can be empty or contain delimiter-separated values)
        delimiter: String delimiter used to separate list items (default: "||")

    Returns:
        str: Combined list as delimited string, empty if no valid items

    Examples:
        combine_list("item1", "item2") -> "item1||item2"
        combine_list("item1||item2", "item3") -> "item1||item2||item3"
        combine_list("", "item1") -> "item1"
        combine_list("item1", "") -> "item1"
        combine_list("item1||item2", "item1||item3") -> "item1||item2||item3"
    """
    if not list_string1 and (not list_string2):
        return ""
    if not list_string1:
        return list_string2
    if not list_string2:
        return list_string1
    if list_string1 == list_string2:
        return list_string1
    items1 = [item.strip() for item in list_string1.split(delimiter) if item.strip()]
    items2 = [item.strip() for item in list_string2.split(delimiter) if item.strip()]
    combined = list(set(items1 + items2))
    return delimiter.join(combined)


def generate_metadata(content: str, metadata: dict | None) -> dict:
    if not metadata:
        metadata = {}
    creation_time = get_current_timestamp()
    metadata["creation_time"] = creation_time
    if isinstance(content, dict) and "image" in content:
        image_urls = []
        for img_part in content["image"]:
            if img_part.get("type") == "image_url":
                url = img_part.get("image_url", {}).get("url")
                if url:
                    image_urls.append(url)
        if image_urls:
            metadata["image_urls"] = image_urls
    return metadata


def delete_candidate_memory(candidate: MemoryEntry, memory_store: MemoryStoreBase):
    """
    Delete candidate memory and its cue indices from the memory store.
    Args:
        candidate: MemoryEntry object representing the candidate memory to delete
        memory_store: MemoryStoreBase instance to perform deletions on
    """
    candidate_index = candidate.index
    cue_indices: str = candidate.cue_indices
    memory_store.delete(candidate_index)
    if cue_indices:
        for cue_index in cue_indices.split("||"):
            index_entry = memory_store.get(cue_index)
            if not index_entry:
                raise ValueError(
                    f"Index entry '{cue_index}' not found in memory store. Please check."
                )
            memory_store.delete(cue_index)


def generate_history(entry: MemoryEntry, best_candidate: MemoryEntry) -> list[dict]:
    if best_candidate.history:
        history = best_candidate.history
    else:
        history = [
            {
                "index": best_candidate.index,
                "value": best_candidate.value,
                "creation_time": best_candidate.creation_time,
                "timestamp": best_candidate.timestamp,
            }
        ]
    history += [
        {
            "index": entry.index,
            "value": entry.value,
            "creation_time": entry.creation_time,
            "timestamp": entry.timestamp,
        }
    ]
    return history


def convert_memory_output(
    memories: Any, metadata: dict, enable_cue_index: bool
) -> list[MemoryEntry]:
    memory_entries = []
    episodic_memory_id = metadata.get("episodic_memory_id", None)
    for memory_output in memories.entries:
        cue_indices = ""
        if enable_cue_index and hasattr(memory_output, "cue_indices") and memory_output.cue_indices:
            cue_indices = "||".join(memory_output.cue_indices)
        episodic_memory_ids = [episodic_memory_id] if episodic_memory_id else []
        entry = MemoryEntry(
            memory_type=memory_output.memory_type,
            index=memory_output.index,
            value=memory_output.value,
            creation_time=metadata["creation_time"],
            timestamp=metadata.get("timestamp", ""),
            cue_indices=cue_indices,
            episodic_memory_ids=episodic_memory_ids,
        )
        memory_entries.append(entry)
    return memory_entries


def dedup_memories(memories: list[MemoryEntry]) -> list[MemoryEntry]:
    """
    Deduplicate a list of MemoryEntry objects based on their index.

    This function removes duplicate memory entries by keeping only the first
    occurrence of each unique index. The order of the first occurrences is preserved.

    Args:
        memories: List of MemoryEntry objects that may contain duplicates

    Returns:
        Deduplicated list of MemoryEntry objects

    Example:
        memories = [entry1, entry2, entry1, entry3]  # entry1 appears twice
        unique_memories = dedup(memories)  # Returns [entry1, entry2, entry3]
    """
    seen_indices = set()
    deduped = []
    for memory in memories:
        if memory.index and memory.index not in seen_indices:
            seen_indices.add(memory.index)
            deduped.append(memory)
        elif not memory.index:
            deduped.append(memory)
    return deduped
