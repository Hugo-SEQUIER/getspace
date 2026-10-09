"""Errors the retrieval harness raises. Kept separate so every module can import them.

Parameters: none.
What it does: names the failure modes of corpus loading, strategy selection and
measurement so a caller can tell a bad corpus from a broken run.
Output: the exception classes.
"""


class EvaluationError(Exception):
    """The harness cannot produce a trustworthy number."""


class CorpusError(EvaluationError):
    """The corpus file is missing, malformed or internally inconsistent."""


class UnknownStrategy(EvaluationError):
    """No strategy is registered under that name."""
