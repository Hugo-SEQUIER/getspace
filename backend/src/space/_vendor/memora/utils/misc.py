# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
# Space adaptations are documented in ../README.md.

import hashlib
from datetime import datetime
from typing import Any


def index_to_id(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def normalize_content(
    content: str | list[str] | list[dict[str, Any]], multimodal_support: bool = True
):
    """
        Normalize context into a standardized format for processing.

        This function handles both text-only and multimodal content, converting them into
        formats that can be easily consumed by downstream components.

        Args:
            context: Input context in various formats:
                - str: Simple text content
                - List[str]: Multiple text strings
                - List[Dict]: Conversation messages (may include multimodal content)

        Returns:
            For text-only content: str
                Example: {
                    "text": "User: Hello
    Assistant: Hi there!"
                }

            For multimodal content: Dict[str, Any]
                Example: {
                    "text": "User: Look at this photo
    Assistant: Nice picture!",
                    "image": [{"type": "image_url", "image_url": {"url": "https://..."}}]
                }

        Raises:
            ValueError: If context format is not supported
    """
    text_parts = []
    image_parts = []
    if isinstance(content, str):
        text_parts.append(content.strip())
    elif isinstance(content, list):
        if all(isinstance(item, str) for item in content):
            text_parts.extend(content)
        elif all(isinstance(item, dict) for item in content):
            if any("role" in item and "content" in item for item in content):
                for msg in content:
                    if isinstance(msg, dict) and "content" in msg:
                        msg_content = msg["content"]
                        if msg.get("role"):
                            msg_content = f"{msg['role']}: {msg_content}"
                        if isinstance(msg_content, list):
                            for part in msg_content:
                                if isinstance(part, dict):
                                    if part.get("type") == "text":
                                        text_parts.append(part.get("text", ""))
                                    elif part.get("type") == "image_url":
                                        image_parts.append(part)
                        elif isinstance(msg_content, str):
                            text_parts.append(msg_content)
            else:
                text_parts.extend([str(item) for item in content])
        else:
            raise ValueError("Context list must contain either all strings or all dictionaries")
    else:
        raise ValueError("Context must be a string, list of strings, or list of dictionaries")
    if image_parts and multimodal_support:
        return {"text": "\n".join(text_parts), "image": image_parts}
    else:
        return {"text": "\n".join(text_parts)}


def context_to_str(context: str | list[str] | list[dict[str, str]]):
    """
    Legacy function - converts context to string format.
    For backward compatibility, but consider using normalize_context for new code.
    Always returns string format (images are ignored).
    """
    result = normalize_content(context, multimodal_support=False)
    return result["text"]


def add_and_condition(where: dict | None, new_condition: dict) -> dict:
    if where is None:
        return new_condition
    if "$and" in where:
        where["$and"].append(new_condition)
        return where
    return {"$and": [where, new_condition]}


def get_current_timestamp() -> str:
    """
    Get the current timestamp as a formatted string in ISO 8601 format with UTC timezone.

    Returns:
        Current timestamp in ISO format with UTC timezone (YYYY-MM-DDTHH:MM:SS.mmmZ)
    """
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def extract_user_id_from_where(where: dict | None) -> str | None:
    """
    Extract user_id from a where clause dictionary.

    Args:
        where: Dictionary representing filter conditions
    Returns:
        user_id string if found, else None
    """
    if where is None:
        return None
    if "user_id" in where:
        return where["user_id"]
    if "$and" in where:
        for condition in where["$and"]:
            if "user_id" in condition:
                return condition["user_id"]
    return None


def merge_metadata(segment_metadata: dict | None, user_metadata: dict | None) -> dict:
    """
    Merge segment metadata with user-provided metadata.

    Args:
        segment_metadata: Metadata from the segment (e.g., heading paths, structure)
        user_metadata: User-provided metadata

    Returns:
        Merged metadata dictionary with user metadata taking precedence
    """
    merged_metadata = {}
    if segment_metadata:
        merged_metadata.update(segment_metadata)
    if user_metadata:
        merged_metadata.update(user_metadata)
    return merged_metadata


def extension_to_type(extension: str) -> str:
    """
    Map file extension to file type for memory builder selection.

    Args:
        extension: File extension (e.g., 'txt', 'docx', 'pdf')

    Returns:
        File type string for memory builder
    """
    extension = extension.lower().strip(".")
    extension_mapping = {
        "txt": "text",
        "md": "markdown",
        "markdown": "markdown",
        "doc": "word",
        "docx": "word",
        "pdf": "pdf",
        "rtf": "text",
        "xls": "excel",
        "xlsx": "excel",
        "csv": "table",
        "ppt": "powerpoint",
        "pptx": "powerpoint",
        "html": "html",
        "htm": "html",
        "xml": "xml",
        "json": "json",
        "yaml": "yaml",
        "yml": "yaml",
        "py": "text",
        "js": "text",
        "ts": "text",
        "java": "text",
        "cpp": "text",
        "c": "text",
        "h": "text",
        "css": "text",
        "sql": "text",
    }
    return extension_mapping.get(extension, "text")
