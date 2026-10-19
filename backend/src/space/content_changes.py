"""Records which kinds of content a write changed, beside the Space content revision.

Browsers compare these per kind, so a write reloads only the lists showing that kind.
"""

ITEM_KINDS = ("project", "folder", "database", "category", "section")
RESOURCE_KINDS = ("document", "task", "memory")
KINDS = (*RESOURCE_KINDS, *ITEM_KINDS, "columns")

# Each kind records the content revision it last changed at, so one statement bumps the
# Space revision and stamps every changed kind with the new value.
BUMP = (
    "UPDATE space.spaces SET content_revision=content_revision+1,"
    "change_revisions=change_revisions||"
    "(SELECT jsonb_object_agg(kind,content_revision+1) FROM unnest(%s::text[]) kind)"
)
BUMP_SPACE = (
    "UPDATE space.spaces SET content_revision=content_revision+1,"
    "change_revisions=change_revisions||"
    "(SELECT jsonb_object_agg(kind,content_revision+1) FROM unnest(%s::text[]) kind) "
    "WHERE organization_id=%s AND id=%s"
)


def resource_kinds(kind, memory_kind=None):
    """Kinds a document, task or memory write changes.

    Memory listings show whether a memory's source changed, so a document or task
    write also changes them.
    """
    if memory_kind == "memory":
        return ("memory",)
    return ("task" if kind in {"record", "task"} else "document", "memory")


def changed(connection, kinds=KINDS, space=None):
    """Bump the content revision and record `kinds` as changed.

    `space` is an (organization_id, space_id) pair for callers outside a scoped
    transaction; scoped callers rely on row-level security to select their Space.
    """
    unknown = set(kinds) - set(KINDS)
    if unknown or not kinds:
        raise ValueError(f"Unknown content kinds: {sorted(unknown)}")
    if space is None:
        connection.execute(BUMP, (list(kinds),))
    else:
        connection.execute(BUMP_SPACE, (list(kinds), *space))
