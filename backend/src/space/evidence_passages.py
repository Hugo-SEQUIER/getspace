"""Parameters: retrieved evidence candidates and passage budgets.

What it does: shapes lossless source views while keeping authored claims indivisible.
Output: complete passage views or an explicit all-candidate fallback reason.
"""

import re
from itertools import pairwise

from space.memory import document_text

# Structured blocks need their surrounding context, not sentence-level slicing.
STRUCTURED = re.compile(r"(?m)^(?:[ \t]*(?:`{3,}|~{3,})|(?: {4}|\t)\S)|\|")
PARAGRAPH_END = re.compile(r"\r?\n[ \t]*\r?\n(?:[ \t]*\r?\n)*")
SENTENCE_END = re.compile(r"[.!?][\"'’”)]*\s+(?=[A-ZÀ-ÖØ-Þ])")


def shape(items, *, max_chars=500, max_units=50, diagnostics=None):
    """Parameters: original candidates, hard bounds and optional content-free diagnostics.

    What it does: packs complete contiguous paragraphs or sentences without edits.
    Any unsafe or incomplete representation rejects the entire shaped candidate set.
    Output: (original-identity passage views, None), or (None, fallback reason).
    """
    if diagnostics is None:
        diagnostics = {}
    for key in ("fallback_detail", "fallback_limit", "fallback_observed"):
        diagnostics.pop(key, None)
    views = []
    for item in items:
        body = item["body"]
        # Removing encoded images would invalidate raw-source passage offsets.
        # Use the existing excerpt fallback, which removes bytes before budgeting.
        if document_text(body) != body:
            return None, "unsafe_structure"
        if not body.strip():
            return None, "blank"
        if len(body) <= max_chars:
            views.append(item)
        else:
            if item.get("entry_id") is not None:
                diagnostics.update(
                    fallback_detail="authored_claim_too_long",
                    fallback_limit=max_chars,
                    fallback_observed=len(body),
                )
                return None, "size"
            if STRUCTURED.search(body):
                return None, "unsafe_structure"
            boundaries = [0, *(m.end() for m in PARAGRAPH_END.finditer(body)), len(body)]
            units = []
            for start, end in pairwise(boundaries):
                if start == end:
                    continue
                if end - start <= max_chars:
                    units.append((start, end))
                    continue
                sentence_ends = [
                    start,
                    *(m.end() for m in SENTENCE_END.finditer(body, start, end)),
                    end,
                ]
                for left, right in pairwise(sentence_ends):
                    if right - left > max_chars:
                        diagnostics.update(
                            fallback_detail="source_unit_too_long",
                            fallback_limit=max_chars,
                            fallback_observed=right - left,
                        )
                        return None, "size"
                    units.append((left, right))
            packed = []
            for start, end in units:
                if packed and end - packed[-1][0] <= max_chars:
                    packed[-1] = (packed[-1][0], end)
                else:
                    packed.append((start, end))
            for start, end in packed:
                if not body[start:end].strip():
                    return None, "blank"
                views.append(
                    item | {"body": body[start:end], "passage": {"start": start, "end": end}}
                )
        if len(views) > max_units:
            diagnostics.update(
                fallback_detail="too_many_passages",
                fallback_limit=max_units,
                fallback_observed=len(views),
            )
            return None, "size"
    return views, None
