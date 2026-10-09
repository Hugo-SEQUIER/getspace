"""Searchable fingerprints of a resource, so recall selects candidates in one SQL query.

Terms are keyed: the fingerprint of a word is an HMAC under a subkey derived from the
Space key. The tokenizer is the one memory recall already uses.

Why this exists: without an index, every recall would read the metadata and body of every
resource in the Space. A fingerprinted term array beside the resource lets the database
select candidates, and the code read only what it will rank and show.
"""

from __future__ import annotations

import hmac
from collections.abc import Iterable, Sequence
from hashlib import sha256

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from space.memory import document_text, tokens

# Truncated to 128 bits: enough to make an accidental collision irrelevant at any scale
# this platform reaches, and half the index size of a full digest. The stored arrays are
# the largest readable addition to a Space, so their width matters.
TERM_BYTES = 16

# A resource's term arrays are bounded so one long document cannot make the index
# unbounded. Terms are kept in first-appearance order, so the cap drops the least
# frequently seen tail of a very long document rather than an arbitrary slice.
MAX_INDEXED_TERMS = 512
MAX_OCCURRENCES = 32767

FIELD_METADATA = 0
FIELD_BODY = 1


def term_key(space_material: bytes) -> bytes:
    """Derive the term key from the Space key.

    Parameters: the 32-byte Space key material.
    What it does: runs HKDF-SHA256 with a fixed purpose string, so term fingerprints
        cannot be reused as, or confused with, any other keyed value of the Space.
    Output: a 32-byte subkey.
    """
    return HKDF(
        algorithm=hashes.SHA256(), length=32, salt=None, info=b"space.resource_terms.v1"
    ).derive(space_material)


def fingerprint(term: str, key: bytes) -> bytes:
    """Fingerprint one token.

    Parameters: a normalized token and the term key.
    What it does: HMAC-SHA256 of the token under the key, truncated to TERM_BYTES.
    Output: the bytes stored in the term array.
    """
    return hmac.new(key, term.encode(), sha256).digest()[:TERM_BYTES]


def fingerprints(text: str, key: bytes) -> tuple[list[bytes], list[int], int]:
    """Fingerprint a field of text.

    Parameters: the text of one field and the term key.
    What it does: tokenizes with the recall tokenizer, counts occurrences in first
        appearance order, and stops adding new terms at MAX_INDEXED_TERMS. Occurrence
        counts are capped at MAX_OCCURRENCES, which is far above any real document.
    Output: (term fingerprints, matching occurrence counts, true token count).
    """
    seen: dict[str, int] = {}
    order: list[str] = []
    total = 0
    for token in tokens(text):
        total += 1
        if token not in seen:
            if len(order) >= MAX_INDEXED_TERMS:
                continue
            seen[token] = 0
            order.append(token)
        seen[token] = min(seen[token] + 1, MAX_OCCURRENCES)
    return [fingerprint(token, key) for token in order], [seen[t] for t in order], total


def resource_fingerprints(
    title: str, topics: Sequence[str], body: str, key: bytes
) -> list[tuple[int, list[bytes], list[int], int]]:
    """Fingerprint everything searchable about one resource revision.

    Parameters: the title, its topic labels, the body text and the term key.
    What it does: fingerprints two fields. Metadata is the title and topic labels
        joined, which is exactly what metadata ranking reads today; the body is the
        source text. A resource is found by either field, so a title match no longer
        needs a matching body to be returned.
    Output: one row per field as (field, fingerprints, occurrence counts, token count).
    """
    metadata = " ".join([title, *topics])
    return [
        (FIELD_METADATA, *fingerprints(metadata, key)),
        (FIELD_BODY, *fingerprints(document_text(body), key)),
    ]


def query_fingerprints(text: str, key: bytes) -> list[bytes]:
    """Fingerprint the words of a query.

    Parameters: the query text and the term key.
    What it does: tokenizes with the same tokenizer and deduplicates, so repeating a
        word does not weight it more than once in candidate selection.
    Output: the fingerprints to match against the stored arrays.
    """
    return list(dict.fromkeys(fingerprint(token, key) for token in tokens(text)))


def merge_fingerprints(rows: Iterable[tuple[int, list[bytes], list[int], int]]) -> dict:
    """Summarize fingerprint rows for reporting or testing.

    Parameters: rows as returned by resource_fingerprints.
    What it does: counts terms per field without exposing term text.
    Output: {field: {"terms": n, "tokens": n}}.
    """
    return {field: {"terms": len(terms), "tokens": total} for field, terms, _counts, total in rows}
