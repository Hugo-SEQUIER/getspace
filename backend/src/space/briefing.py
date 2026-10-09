"""Bounded, verbatim section extracts for session handoffs; no model or storage access."""

import re

HEADINGS = ("Decisions", "Progress", "Unresolved questions", "Next actions")
COMPACT_CHARS = 1200


def compact_handoff(body: str, maximum: int = COMPACT_CHARS) -> tuple[str, bool]:
    """Reserve space for each handoff section, keeping next actions near the start.

    Only the canonical SaveHandoff layout is rearranged. Short or manually edited
    documents retain their normal prefix excerpt. Every selected passage is copied
    from the source; an ellipsis and the returned flag disclose omitted content.
    """
    if len(body) <= maximum:
        return body, False
    parts = re.split(r"\n\n## (Decisions|Progress|Unresolved questions|Next actions)\n", body)
    if tuple(parts[1::2]) != HEADINGS:
        return body[:maximum], True
    sections = dict(zip(parts[1::2], parts[2::2], strict=True))
    ordered = [("", parts[0])] + [
        ("## " + name + "\n", sections[name])
        for name in ("Next actions", "Unresolved questions", "Decisions", "Progress")
    ]
    ordered = [(heading, text.strip()) for heading, text in ordered if text.strip()]
    overhead = sum(len(heading) for heading, _ in ordered) + 2 * (len(ordered) - 1)
    available = maximum - overhead
    if not ordered or available < len(ordered):
        return body[:maximum], True
    # Share the budget first so long progress cannot erase blockers or decisions.
    sizes = [min(len(text), available // len(ordered)) for _, text in ordered]
    remaining = available - sum(sizes)
    for index, (_, text) in enumerate(ordered):
        extra = min(remaining, len(text) - sizes[index])
        sizes[index] += extra
        remaining -= extra
    selected = []
    for (heading, text), size in zip(ordered, sizes, strict=True):
        passage = text if len(text) <= size else text[: size - 1] + "…"
        selected.append(heading + passage)
    return "\n\n".join(selected), True
