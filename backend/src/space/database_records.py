"""Database records stored one packed row per record, each with its own history.

A database item revision holds the schema and the metadata. Its records live here: one head
row per record and one packed revision per version, keyed by the database revision that
wrote it. An edit stores the rows it changed, so storage grows with what changed rather than
with the size of the table, and a table is bounded by a record count instead of one payload.

Records saved before migration 0042 sit inside their item revision payloads. Reads of those
revisions still take them from the payload, and the database's next write copies its current
records into these tables (`baseline` reports such a payload as `legacy`).
"""

from uuid import UUID, uuid5

from space.workspace_items import MAX_DATABASE_RECORDS, record_values_problem

KEY_COLUMNS = "k.wrapping_provider,k.wrapping_key_reference,k.wrapped_key "
# The current records and the revision they belong to, in one statement, so a concurrent
# write cannot leave the rows and the item revision describing different states.
CURRENT = (
    "SELECT i.current_revision AS database_revision,h.id AS record_id,h.position,"
    "v.revision,v.key_id,v.encrypted_payload," + KEY_COLUMNS + "FROM space.workspace_items i "
    "LEFT JOIN space.database_records h ON (h.organization_id,h.space_id,h.database_id)="
    "(i.organization_id,i.space_id,i.id) AND NOT h.deleted "
    "AND (%s::text[] IS NULL OR h.id=ANY(%s::text[])) "
    "LEFT JOIN space.database_record_revisions v ON (v.organization_id,v.space_id,"
    "v.database_id,v.record_id,v.revision)="
    "(h.organization_id,h.space_id,h.database_id,h.id,h.current_revision) "
    "LEFT JOIN space.space_keys k ON (k.organization_id,k.space_id,k.id)="
    "(v.organization_id,v.space_id,v.key_id) "
    "WHERE i.id=%s ORDER BY h.position"
)
# Every record's last version at or before a revision; tombstones are dropped afterwards.
AT_REVISION = (
    "SELECT DISTINCT ON (v.record_id) v.record_id,h.position,v.revision,v.key_id,"
    "v.encrypted_payload,v.deleted," + KEY_COLUMNS + "FROM space.database_record_revisions v "
    "JOIN space.database_records h ON (h.organization_id,h.space_id,h.database_id,h.id)="
    "(v.organization_id,v.space_id,v.database_id,v.record_id) "
    "JOIN space.space_keys k ON (k.organization_id,k.space_id,k.id)="
    "(v.organization_id,v.space_id,v.key_id) "
    "WHERE v.database_id=%s AND v.revision<=%s ORDER BY v.record_id,v.revision DESC"
)
HEADS = (
    "SELECT id FROM space.database_records WHERE database_id=%s "
    "AND (%s::text[] IS NULL OR id=ANY(%s::text[]))"
)
COUNTS = (
    "SELECT database_id,count(*) AS n FROM space.database_records "
    "WHERE database_id=ANY(%s::uuid[]) AND NOT deleted GROUP BY database_id"
)


def record_uuid(database_id, record_id):
    """The payload identity of one record: bound to its database and its own ID."""
    return uuid5(UUID(str(database_id)), record_id)


def conform(record, fields):
    """Parameters: a stored record and the schema it is read under.
    What it does: keeps the values the schema can hold. A field that was removed, or whose
    type or options no longer accept a stored value, reads as empty until the row is next
    written; the stored history keeps the value.
    Output: the record with its conforming values.
    """
    by_id = {field["id"]: field for field in fields}
    values = {}
    for key, value in record.get("values", {}).items():
        field = by_id.get(key)
        if field is not None and record_values_problem({key: value}, {key: field}) is None:
            values[key] = value
    return {**record, "notes": record.get("notes", ""), "values": values}


def canonical(record):
    """The comparable content of a record; a missing value and a null value are the same."""
    return (
        record["name"],
        record.get("notes", ""),
        record.get("createdAt"),
        record["updatedAt"],
        tuple(sorted((k, v) for k, v in record.get("values", {}).items() if v is not None)),
    )


def _keys(workspace, scope, rows):
    keys = {}
    for row in rows:
        if row["record_id"] is not None and row["key_id"] not in keys:
            keys[row["key_id"]] = workspace._unwrap_key(
                scope["organization_id"],
                scope["space_id"],
                {
                    "id": row["key_id"],
                    "wrapping_provider": row["wrapping_provider"],
                    "wrapping_key_reference": row["wrapping_key_reference"],
                    "wrapped_key": row["wrapped_key"],
                },
            )
    return keys


def decode(workspace, scope, database_id, rows, fields):
    """Parameters: scope, database, raw rows from CURRENT or AT_REVISION and the schema.
    What it does: unpacks each record and conforms it to the schema; no model calls.
    Output: the records in stored order.
    """
    keys = _keys(workspace, scope, rows)
    records = []
    for row in sorted(
        (row for row in rows if row["record_id"] is not None and not row.get("deleted")),
        key=lambda row: row["position"],
    ):
        record = workspace._unpack(
            keys[row["key_id"]],
            scope,
            record_uuid(database_id, row["record_id"]),
            row["revision"],
            "database_record",
            row["encrypted_payload"],
        )
        records.append(conform(record, fields))
    return records


def current_rows(connection, database_id, ids=None):
    """The current rows of one database, with the database revision they belong to."""
    ids = list(ids) if ids is not None else None
    return connection.execute(CURRENT, (ids, ids, database_id)).fetchall()


def rows_at(connection, database_id, revision):
    return connection.execute(AT_REVISION, (database_id, revision)).fetchall()


def counts(connection, database_ids):
    """Record counts for databases whose rows live in these tables; absent means none."""
    ids = [str(id_) for id_ in database_ids]
    if not ids:
        return {}
    rows = connection.execute(COUNTS, (ids,)).fetchall()
    return {str(row["database_id"]): row["n"] for row in rows}


def baseline(workspace, connection, scope, database_id, current_data, ids=None):
    """Parameters: the writing transaction, the database, its unpacked current payload data
    and optionally the record IDs the write touches.
    What it does: reads what the current revision holds for those records. A payload that still
    carries `records` is a database written before migration 0042: all of its records are the
    baseline, and the write copies every surviving one into the row tables.
    Output: (records by ID, IDs with a head row, legacy, how many records the database holds).
    """
    if current_data is None:
        return {}, set(), False, 0
    fields = current_data.get("fields", [])
    if current_data.get("records") is not None:
        before = {r["id"]: conform(r, fields) for r in current_data["records"]}
        return before, set(), True, len(before)
    rows = current_rows(connection, database_id, ids)
    records = decode(workspace, scope, database_id, rows, fields)
    wanted = list(ids) if ids is not None else None
    heads = connection.execute(HEADS, (database_id, wanted, wanted)).fetchall()
    total = (
        len(records)
        if ids is None
        else connection.execute(
            "SELECT count(*) AS n FROM space.database_records WHERE database_id=%s AND NOT deleted",
            (database_id,),
        ).fetchone()["n"]
    )
    return {r["id"]: r for r in records}, {row["id"] for row in heads}, False, total


def plan(before, upserts, deletes, *, legacy, total, limit=None):
    """Parameters: the baseline records by ID, the records to write, the IDs to delete,
    whether the baseline came from a pre-migration payload and how many records the database
    holds before the write.
    What it does: decides which rows a write stores. An upsert equal to its stored record
    stores nothing; a deletion of an unknown ID stores nothing. A legacy baseline stores every
    surviving record, so the database's current records all live in the row tables afterwards.
    Output: (writes as (record ID, record or None), events as (record ID, before, after),
    counts of inserted, updated and deleted records).
    """
    from space.workspace import CommandError

    limit = MAX_DATABASE_RECORDS if limit is None else limit
    deleted = {id_ for id_ in deletes if id_ in before}
    writes, events = [], []
    counts = {"inserted": 0, "updated": 0, "deleted": 0}
    touched = set()
    for record in upserts:
        id_ = record["id"]
        touched.add(id_)
        previous = before.get(id_)
        if previous is None:
            counts["inserted"] += 1
        elif canonical(previous) == canonical(record):
            if not legacy:
                continue
            writes.append((id_, record))
            continue
        else:
            counts["updated"] += 1
        writes.append((id_, record))
        events.append((id_, previous, record))
    for id_ in deleted:
        if id_ in touched:
            continue
        touched.add(id_)
        counts["deleted"] += 1
        writes.append((id_, None))
        events.append((id_, before[id_], None))
    if legacy:
        writes.extend((id_, record) for id_, record in before.items() if id_ not in touched)
    if total + counts["inserted"] - counts["deleted"] > limit:
        raise CommandError(f"A database holds at most {limit:,} records", 400)
    return writes, events, counts


def snapshot_changes(before, records):
    """Parameters: the baseline records by ID and the full list a snapshot save sent.
    What it does: turns a whole-table save into the row changes it implies. Rows keep their
    stored order; a snapshot cannot reorder them.
    Output: (upserts, deletes) for `plan`.
    """
    sent = {record["id"] for record in records}
    upserts = [
        record
        for record in records
        if record["id"] not in before or canonical(before[record["id"]]) != canonical(record)
    ]
    return upserts, [id_ for id_ in before if id_ not in sent]


def write(workspace, connection, scope, key, database_id, revision, writes, existing):
    """Parameters: the writing transaction, the Space key, the database, the item revision
    being written (already inserted) and the planned writes with the IDs that have a head row.
    What it does: appends one packed revision per written record and moves each head to it;
    a deletion appends a tombstone without a payload. New rows take the next positions.
    Output: nothing; a rolled-back transaction leaves no row.
    """
    if not writes:
        return
    org, sid = scope["organization_id"], scope["space_id"]
    position = connection.execute(
        "SELECT coalesce(max(position),0) AS n FROM space.database_records WHERE database_id=%s",
        (database_id,),
    ).fetchone()["n"]
    for record_id, record in writes:
        deleted = record is None
        if record_id in existing:
            connection.execute(
                "UPDATE space.database_records SET current_revision=%s,deleted=%s "
                "WHERE database_id=%s AND id=%s",
                (revision, deleted, database_id, record_id),
            )
        elif deleted:
            # A record that never reached the row tables leaves no trace when it goes.
            continue
        else:
            position += 1
            connection.execute(
                "INSERT INTO space.database_records (organization_id,space_id,database_id,id,"
                "current_revision,deleted,position) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (org, sid, database_id, record_id, revision, False, position),
            )
        payload = (
            None
            if deleted
            else workspace._pack(
                key,
                scope,
                record_uuid(database_id, record_id),
                revision,
                "database_record",
                record,
            )
        )
        connection.execute(
            "INSERT INTO space.database_record_revisions (organization_id,space_id,"
            "database_id,record_id,revision,key_id,encrypted_payload,deleted) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (org, sid, database_id, record_id, revision, key.key.key_id, payload, deleted),
        )
