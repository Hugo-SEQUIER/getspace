"""Identity hashes shared by account erasure and bootstrap."""

import hashlib
import json


def subject_hash(issuer, subject):
    return hashlib.sha256(json.dumps([issuer, subject], separators=(",", ":")).encode()).digest()
