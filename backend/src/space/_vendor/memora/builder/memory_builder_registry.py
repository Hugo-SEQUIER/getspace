# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
# Space adaptations are documented in ../README.md.

from typing import Any as DictConfig

from space._vendor.memora.builder.memory_builder import MemoryBuilder
from space._vendor.memora.core.memory import AgentMemory
from space._vendor.memora.utils.llm import ChatCompletionModel


class MemoryBuilderRegistry:
    _builders: dict[str, type[MemoryBuilder]] = {}

    @classmethod
    def register(cls, file_type: str, builder_cls: type[MemoryBuilder]):
        cls._builders[file_type] = builder_cls

    @classmethod
    def get(
        cls,
        file_type: str,
        cfg: DictConfig,
        agent_memory: AgentMemory,
        model_client: ChatCompletionModel,
    ) -> MemoryBuilder:
        try:
            return cls._builders[file_type](cfg, agent_memory, model_client)
        except KeyError:
            raise ValueError("No memory builder registered for this source type") from None
