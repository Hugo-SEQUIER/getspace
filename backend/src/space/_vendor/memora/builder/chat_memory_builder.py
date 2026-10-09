# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
# Space adaptations are documented in ../README.md.

from __future__ import annotations

import logging
from typing import Any as DictConfig

from pydantic import BaseModel, Field

from space._vendor.memora.builder.memory_builder import (
    PROMPT_BUILD_MEMORY,
    MemoryBuilder,
    MemoryOutputs,
)
from space._vendor.memora.core.memory import AgentMemory
from space._vendor.memora.core.memory_entry import MemoryEntry
from space._vendor.memora.utils.llm import ChatCompletionModel
from space._vendor.memora.utils.memory import convert_memory_output

logger = logging.getLogger(__name__)
PROMPT_EPISODIC_MEMORY = """
You are an expert episodic memory generator that creates episodic memory summaries from conversation segments.

# TASK:
Your task is to generate an episodic memory with an index and a detailed summary based on the provided conversation segment.

Generate the episodic memory in the following format:
EpisodicIndex: [6-8 word summary that captures the main topic, entity or event of the episode]
EpisodicValue: [1-3 sentence of descriptive summary of the conversation]

# GUIDELINES:
1. The EpisodicIndex
- Create a short index (6-8 words) that captures the main topic or event of the episode.
- Always include the specific context (e.g., domain, or entity) from the source text in the Index to avoid vague terms.

2. The EpisodicValue
- Generate an episodic summary (1-3 sentences) that captures:
  - The main information of the conversation segment, including the main topic, theme, or event being discussed.
  - Relevant participants in the conversation, refer to the participants by their names if available.
  - Use the original wordings from the conversation when possible.
- Focus on "what happened" rather than specific details. The summary is meant to provide context for future retrieval.
- Make the summary self-contained and understandable without the original conversation.
- If images are present, consider the visual content as part of the textual context.
- Use only information present in the conversation segment to generate the summary; do not add external knowledge or infer beyond the content.
- If the conversation is between a user and an AI assistant, focus on the user's inputs and the overall context rather than the assistant's responses.

Input Conversation Segment:
{content}

Output:
"""


class EpisodicMemoryOutput(BaseModel):
    episodic_index: str = Field(
        description="A short 6-8 word summary that captures the main topic, entity or event of the episode"
    )
    episodic_value: str = Field(
        description="A detailed 1-4 sentence episodic summary providing context and narrative"
    )


class ChatMemoryBuilder(MemoryBuilder):
    def __init__(
        self, cfg: DictConfig, agent_memory: AgentMemory, model_client: ChatCompletionModel
    ):
        super().__init__(cfg, agent_memory, model_client)

    def generate_memory_entries(
        self, content: str | dict | None, metadata: dict | None, enable_cue_index: bool = False
    ) -> list[MemoryEntry]:
        """
        Build memory from content.

        Args:
            content: The content to extract memories from
            enable_cue_index: If True, generate cue indices in a separate LLM call. If False, use original format.

        Returns:
            memory entries extracted from the content
        """
        build_memory_prompt = PROMPT_BUILD_MEMORY
        response_format = MemoryOutputs
        memories = self.handle_multimodal_content(
            content, metadata, build_memory_prompt, response_format
        )
        timestamp = metadata.get("timestamp", "N/A") if metadata else "N/A"
        timestamp = timestamp.split(" on ")[-1] if " on " in timestamp else timestamp
        if not memories:
            memories = self._model_client.invoke(
                input=build_memory_prompt,
                prompt_args={"content": content, "timestamp": timestamp},
                response_format=response_format,
            )
        memory_entries = convert_memory_output(memories, metadata, enable_cue_index=False)
        for entry in memory_entries:
            entry.memory_type = "factual"
        if enable_cue_index and memory_entries:
            try:
                memories_batch = [
                    {"index": entry.index, "value": entry.value} for entry in memory_entries
                ]
                cue_indices_map = self.cue_index_generator.generate_cue_indices_batch(
                    memories_batch
                )
                for entry in memory_entries:
                    cue_indices = cue_indices_map.get(entry.index, [])
                    entry.cue_indices = (
                        "||".join(cue_indices) if cue_indices and len(cue_indices) > 0 else ""
                    )
            except Exception:
                raise RuntimeError("Memora processing failed") from None
        return memory_entries

    def generate_episodic_memory(
        self, content: str | dict | None, metadata: dict | None, segment_as_ep_mem: bool = True
    ) -> MemoryEntry | None:
        """
        Generate episodic memory using the original conversation segment text.

        Args:
            content: The conversation content (text or multimodal dict from normalize_content)
            metadata: Metadata for the episodic memory

        Returns:
            MemoryEntry for episodic memory using the original conversation segment
        """
        try:
            if isinstance(content, dict) and "text" in content:
                content_text = content["text"]
            else:
                content_text = content
            segment_topic = (
                metadata.get("segment_topic", "conversation") if metadata else "conversation"
            )
            segment_index = metadata.get("segment_index", 0) if metadata else 0
            timestamp = metadata.get("timestamp", "") if metadata else ""
            original_text = content_text
            if not segment_as_ep_mem:
                episodic_output = self._model_client.invoke(
                    input=PROMPT_EPISODIC_MEMORY,
                    prompt_args={"content": content_text},
                    response_format=EpisodicMemoryOutput,
                )
                episodic_index = f"[EPISODIC] {episodic_output.episodic_index}"
                episodic_value = episodic_output.episodic_value
                episodic_entry = MemoryEntry(
                    memory_type="episodic",
                    index=episodic_index,
                    value=episodic_value,
                    original_text=original_text,
                    creation_time=metadata.get("creation_time", ""),
                    timestamp=metadata.get("timestamp", ""),
                )
            else:
                episodic_index = f"[EPISODIC] {segment_topic} (segment {segment_index})"
                episodic_value = content_text
            episodic_entry = MemoryEntry(
                memory_type="episodic",
                index=episodic_index,
                value=episodic_value,
                original_text=original_text,
                creation_time=metadata.get("creation_time", "") if metadata else "",
                timestamp=timestamp,
            )
            return episodic_entry
        except Exception:
            raise RuntimeError("Memora processing failed") from None
