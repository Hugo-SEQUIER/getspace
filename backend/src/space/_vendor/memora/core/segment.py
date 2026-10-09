# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
# Space adaptations are documented in ../README.md.

"""
Simple Segment class for representing text content with metadata and type.
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Segment:
    """
    Simple segment class with content, type, and metadata.
    """

    content: str
    segment_type: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        """String representation of the segment."""
        content_preview = self.content[:100] + "..." if len(self.content) > 100 else self.content
        return f"Segment({self.segment_type}): {content_preview}"

    def __repr__(self) -> str:
        """Detailed string representation."""
        return f"Segment(type={self.segment_type}, content_len={len(self.content)})"
