# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
# Space adaptations are documented in ../README.md.

from typing import Any as DictConfig

from pydantic import BaseModel, Field

from space._vendor.memora.utils.llm import ChatCompletionModel

PROMPT_CUE_GENERATION_SPECIFIC = """
You are a memory-indexing assistant that creates cue indices for a memory system.

# TASK
For each memory provided below, generate 0-3 short, meaningful *CUE INDICES* that can later help recall or reason about that memory.

# GUIDELINES
1. Cue index should add coverage by focusing on perspectives in the memory value that the primary index does not capture.
2. Each **cue index** represents a compact semantic key for recalling the memory. It should be:
   - Compact (2-5 words)
   - Semantically rich — include meaningful words such as *actor*, *action*, *concept*, or *context*
   - Contextually anchored — specify *who* or *what domain* the memory belongs to.
   - Generalizable — written in natural language so that semantically similar queries can retrieve it  
   - Distinct — each cue should cover a different aspect (e.g., actor, action, goal, cause or setting)
3. If relevant, include both:  
   - Factual cues — concrete entities, actions, or objects
   - Relational cues — causal or intentional relations
4. Avoid  
   - Single generic words without context (e.g., "appreciation", "learning")  
   - Copying full sentences or repeating the original memory text  
5. Typical Patterns  
   - `[Actor] [Concept or Emotion]` → "Caroline family gratitude"  
   - `[Actor] [Action or Event]` → "Melanie birthday concert"  
   - `[Actor] [Object] [Relation]` → "Melanie necklace meaning"  
   - `[Domain] [Event or Topic]` → "Project Phoenix kickoff"  
   - `[Concept] [Purpose or Theme]` → "LLM retraining with feedback"
6. If the primary index already captures all aspects of the memory value, you may return an empty list.

# EXAMPLES
Primary Index: "LLM Epsilon retraining"
Memory Value: "After receiving user feedback, the LLM Epsilon was retrained to reduce hallucinations and improve accuracy."
Cue indices: ["LLM Epsilon hallucination reduction", "LLM Epsilon user feedback incorporation"]

Primary Index: "Jim rolls back Phoenix deployment"
Memory Value: "User Jim requested to roll back the deployment of Phoenix after detecting a regression."
Cue indices: ["Phoenix deployment regression"]

Primary Index: "Sarah's hiking trip to the Grand Canyon"
Memory Value: "Sarah went on a hiking trip to the Grand Canyon last summer and enjoyed the scenic views."
Cue indices: []

Primary Index: "Jane updated Project Nexus timeline"
Memory Value: "Jane updated the Project Nexus timeline after client feedback and flagged resource constraints for the next sprint."
Cue indices: ["Project Nexus resource constraints", "Project Nexus client feedback", "Project Nexus sprint planning"]


# MEMORIES TO PROCESS
{memories}

"""
PROMPT_CUE_GENERATION_MEDIUM = """
You are a memory-indexing assistant that creates cue indices for a memory system.

# TASK
For each memory provided below, generate 1-3 short, meaningful *CUE INDICES* that can later help recall or reason about that memory.
Provide the cue indices as a list of strings for each memory.

# GUIDELINES
1. Cue indices are used to enhance memory retrieval by providing additional semantic keys beyond the primary index.
2. The cue indices could be:
    - Semantic cues that capture different perspectives in the memory value, beyond what the primary index covers.
    - Contextual cues that relate the memory to main entities, events, themes, or topics, and help in relating the memory to other similar memories.

3. Each **cue index** should be:    
    - They should be compact, ideally 2-4 words long.
    - They should always contain the main entities, domains present in the memory.
    - Each cue should focus on a different aspect of the memory to provide diverse viewpoints.
    - Each cue is formed as two main components: [Main Entity/Domain] + [Concept/Action/Event/Object].
    - Typical Patterns for cue indices include:
        - [Main Entity] [Concept or Emotion] → "Caroline family gratitude"
        - [Domain] [Event or Topic] → "Project Phoenix kickoff"
        - [Main Entity] [Action or Event] → "Sarah live concert"
        - [Main Entity] [Object] → "Alice research paper"

4. Avoid the following when generating cue indices:  
    - Repeating the primary index as a cue index
    - Single generic words without context (e.g., "appreciation", "learning")


# EXAMPLES
Primary Index: "Jane updated Project Nexus timeline"
Memory Value: "Jane updated the Project Nexus timeline after client feedback and flagged resource constraints for the next sprint."
Cue indices: ["Project Nexus timeline", "Project Nexus resource constraints", "Project Nexus client feedback"]

Primary Index: "Sarah attended concert with friends"
Memory Value: "Sarah attended a live concert with her friends last weekend and enjoyed the music and atmosphere."
Cue indices: ["Sarah live concert", "Sarah music enjoyment"]

Primary Index: "John's birthday party planning"
Memory Value: "John planned a surprise birthday party for his friend Maria, including decorations, a cake, and invitations."
Cue indices: ["Maria birthday surprise", "John party decorations", "John cake planning"]

# MEMORIES TO PROCESS
{memories}

"""
PROMPT_CUE_GENERATION = """You are a memory-indexing assistant optimized for knowledge retrieval. Your goal is to create "Cue Indices" that serve as semantic anchors for specific memories.

# TASK
For each memory provided, generate 1-3 short, meaningful CUE INDICES that can later help recall or reason about that memory. Provide the cue indices as a list of strings for each memory.

# GUIDELINES
1. **Definition**: A cue index is a concise phrase (2-4 words) that anchors a specific topic to a memory. It taskes the following structure: [Main Entity] + [Key Aspect].
    - The **Main Entity** is the primary person, domain, or object involved in the memory (the "Who" or "What").
    - The **Key Aspect** specifies the event, preference, action, state, or object associated with the entity.
    Examples of Main Entity + Key Aspect patterns:
        - [Person] + [Event/Activity] → "Jane hiking trip", "Mike vacation"
        - [Person] + [Hobby/Preference] → "Michael Jazz music", "Sophie vegan diet"
        - [Person] + [Condition/State] → "Emma career change", "Liam health problems"
        - [Person] + [Object/Relation] → "Alice research paper", "David guitar"
        - [Domain] + [Attribute/Artifact] → "Project Orion timeline", "Product X features"

2. **Specificity**: Avoid generic single words like "summer", "happiness", or "project meeting". Every cue index must be contextually anchored to a the main entity including person, event, or domain mentioned in the memory. For example, instead of "hiking," use "Sarah hiking." And the key aspect should reflect a concrete topic rather than vague concepts. For example, use "Mike mental health problems" instead of "Mike feelings."
3. **Atomicity**: Each cue index must represent a single, indivisible aspect. Do not overload a cue with timestamps, specific numbers, or multiple descriptors. For example, use "Mike birthday party" instead of "Mike birthday party 2023". Avoid overspecification that limits generalizability.
4. **Distinct Facets**: A memory could have multiple cue indices, each focusing on a different aspect of the memory to provide diverse viewpoints. Ideally, cue indices of one memory should not overlap in meaning. Each index must target a completely different dimension of the memory. Avoid generating cue indices that are similar to each other for the same memory. For example, don't create both "Project Phoenix kickoff" and "Project Phoenix launch" for the same memory.
5. **Uniqueness**: Do not repeat the primary memory index as a cue index.
6. **Purpose**: Cue indices could help with recall and reasoning by providing additional semantic keys beyond the primary index. They serve to link related memories together based on shared themes.


# EXAMPLES
Primary Index: "Jane's hiking trip to Appalachian Trail"
Memory Value: "Last summer, Jane went on a week-long hiking trip along the Appalachian Trail. She enjoyed the scenic views and challenging trails."
Cue indices: ["Jane hiking","Appalachian Trail views","Jane summer trip"]

Primary Index: "Mike's surprise birthday party"
Memory Value: "Mike's friends organized a surprise birthday party for him at his favorite restaurant Bistro Max."
Cue indices: ["Mike birthday party", "Mike favorite restaurant", "Mike friends gathering"]

Primary Index: "Project Orion launch delay"
Memory Value: "The launch of Project Orion has been delayed due to unforeseen technical issues that need to be resolved."
Cue indices: ["Project Orion launch", "Project Orion technical issues"]

Primary Index: "Emma went swimming"
Memory Value: "Emma went swimming during her vacation".
Cue indices: ["Emma swimming"]

# MEMORIES TO PROCESS
{memories}

"""


class MemoryCueIndices(BaseModel):
    memory_index: str = Field(description="The primary memory index", max_length=300)
    cue_indices: list[str] = Field(
        description="List of cue indices generated for this memory", max_length=3
    )


class BatchCueIndices(BaseModel):
    results: list[MemoryCueIndices] = Field(description="List of cue indices for each memory")


class CueIndexGenerator:
    def __init__(self, cfg: DictConfig, model_client: ChatCompletionModel):
        self.cfg = cfg
        self._model_client = model_client
        pass

    def generate_cue_indices_batch(self, memories: list[dict[str, str]]) -> dict[str, list[str]]:
        """
        Generate cue indices for multiple memories in a single LLM call.

        Args:
            memories: List of dictionaries with 'index' and 'value' keys

        Returns:
            Dictionary mapping memory indices to their cue indices
        """
        memories_text = ""
        for i, mem in enumerate(memories, 1):
            memories_text += f"\nMemory {i}:\n"
            memories_text += f"Primary Index: {mem['index']}\n"
            memories_text += f"Memory Value: {mem['value']}\n"
        prompt_args = {"memories": memories_text}
        try:
            result: BatchCueIndices = self._model_client.invoke(
                input=PROMPT_CUE_GENERATION,
                prompt_args=prompt_args,
                response_format=BatchCueIndices,
            )
        except Exception:
            raise RuntimeError("Memora processing failed") from None
        cue_indices_map = {}
        for item in result.results:
            cue_indices_map[item.memory_index] = item.cue_indices
        return cue_indices_map

    def generate_cue_indices(self, memory_value: str, primary_index: str) -> list[str]:
        """
        Generate cue indices for a single memory.

        Args:
            memory_value: The memory content
            primary_index: The primary memory index

        Returns:
            List of cue indices
        """
        memories = [{"index": primary_index, "value": memory_value}]
        result = self.generate_cue_indices_batch(memories)
        return result.get(primary_index, [])
