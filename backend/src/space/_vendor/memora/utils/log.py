"""Upstream content logging is disabled at the persistence boundary."""


def log_memory_operation(*args, **kwargs):
    pass


def log_memory_building(*args, **kwargs):
    pass


def log_segments(*args, **kwargs):
    pass
