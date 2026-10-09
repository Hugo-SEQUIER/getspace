"""Space overview. No query text, document contents, or credentials enter telemetry."""

import logging
from datetime import UTC, timedelta
from functools import wraps
from time import perf_counter
from uuid import uuid4

from space import quotas

logger = logging.getLogger(__name__)


def record_write(connection, scope, identity, resource_id, request, memory_kind, index):
    """Called only for a newly committed command, inside its content transaction."""
    kind = memory_kind or ("task" if request.kind == "task" else "document")
    new_items, item_count = int(request.resource_id is None), 1
    if kind == "memory":
        previous = {
            str(row["claim_id"])
            for row in connection.execute(
                "SELECT claim_id FROM space.memory_claims WHERE memory_id=%s", (resource_id,)
            ).fetchall()
        }
        current = {str(claim["id"]) for claim in (index or {}).get("claims", [])}
        new_items, item_count = len(current - previous), len(current)
    connection.execute(
        "UPDATE space.resources SET content_lines=%s WHERE id=%s",
        (len(request.body.splitlines()), resource_id),
    )
    connection.execute(
        "INSERT INTO space.activity_events(organization_id,space_id,id,agent_id,actor_type,"
        "kind,resource_id,new_items,item_count) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (
            scope["organization_id"],
            scope["space_id"],
            uuid4(),
            scope.get("agent_id"),
            "agent" if identity.client_id else "website",
            kind,
            resource_id,
            new_items,
            item_count,
        ),
    )


def record_retrieval(workspace, identity, space_id, request, elapsed, result):
    """Best effort after execution; telemetry failure cannot fail a successful recall."""
    try:
        items = result["items"] if result is not None else []
        claims = sorted({item["entry_id"] for item in items if item.get("entry_id")})
        with workspace.db.transaction(identity) as connection:
            connection.execute("SET LOCAL statement_timeout = '1000ms'")
            connection.execute("SET LOCAL lock_timeout = '250ms'")
            scope = workspace._scope(connection, identity, space_id)
            connection.execute(
                "INSERT INTO space.activity_events(organization_id,space_id,id,agent_id,"
                "actor_type,kind,item_count,outcome,duration_ms,strategy,returned_claims) "
                "VALUES(%s,%s,%s,%s,%s,'retrieval',%s,%s,%s,%s,%s::uuid[])",
                (
                    scope["organization_id"],
                    space_id,
                    uuid4(),
                    scope.get("agent_id"),
                    "agent" if identity.client_id else "website",
                    len(items),
                    "error" if result is None else "success" if items else "empty",
                    max(0, elapsed * 1000),
                    "resume" if request.mode == "resume" else request.strategy,
                    claims,
                ),
            )
    except Exception:
        logger.warning("Overview retrieval activity could not be recorded")


def track_retrieval(function):
    @wraps(function)
    def wrapped(workspace, identity, space_id, request):
        # Authorize before measuring. Rejected identities must not create activity.
        workspace._recheck(identity, space_id)
        started = perf_counter()
        result = None
        try:
            result = function(workspace, identity, space_id, request)
            return result
        finally:
            record_retrieval(
                workspace, identity, space_id, request, perf_counter() - started, result
            )

    return wrapped


TOTALS = """
SELECT count(*) FILTER (WHERE kind='document' AND memory_kind IS NULL) AS documents,
       count(*) FILTER (WHERE kind='record') AS tasks,
       (SELECT count(*) FROM space.memory_claims) AS memories,
       coalesce(sum(content_lines) FILTER (WHERE kind='document' AND memory_kind IS NULL),0)
           AS document_lines,
       count(content_lines) FILTER (WHERE kind='document' AND memory_kind IS NULL)
           AS measured_documents
FROM space.resources
"""

MEASURES = """
count(*) FILTER (WHERE kind='document') AS document_writes,
coalesce(sum(new_items) FILTER (WHERE kind='document'),0) AS documents_created,
coalesce(sum(new_items) FILTER (WHERE kind='memory'),0) AS memories_created,
count(*) FILTER (WHERE kind='memory') AS memory_writes,
count(*) FILTER (WHERE kind='retrieval') AS retrievals,
count(*) FILTER (WHERE kind='retrieval' AND outcome='success') AS with_results,
count(*) FILTER (WHERE kind='retrieval' AND outcome='empty') AS empty,
count(*) FILTER (WHERE kind='retrieval' AND outcome='error') AS errors,
percentile_cont(0.5) WITHIN GROUP (ORDER BY duration_ms)
    FILTER (WHERE kind='retrieval' AND outcome!='error') AS p50_ms,
percentile_cont(0.95) WITHIN GROUP (ORDER BY duration_ms)
    FILTER (WHERE kind='retrieval' AND outcome!='error') AS p95_ms
"""


def snapshot(workspace, identity, space_id, days=30, *, all_periods=False):
    from space.workspace import CommandError

    if days not in (7, 30, 90):
        raise CommandError("Choose 7, 30, or 90 days", 400)
    if all_periods:
        days = 90
    with workspace.db.transaction(identity) as connection:
        workspace._scope(connection, identity, space_id, human=True, api_key=True)
        clock = connection.execute(
            "SELECT activity_started_at,statement_timestamp() AS as_of FROM space.spaces"
        ).fetchone()
        tracking, now = clock["activity_started_at"], clock["as_of"].astimezone(UTC)
        start = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days - 1)
        since = max(start, tracking)
        totals = connection.execute(TOTALS).fetchone()
        where = " FROM space.activity_events WHERE created_at >= %s AND created_at <= %s "
        period = (since, now)
        starts = {
            length: now.replace(hour=0, minute=0, second=0, microsecond=0)
            - timedelta(days=length - 1)
            for length in ((7, 30, 90) if all_periods else (days,))
        }
        # Group all supported windows in the same queries. Percentiles and distinct
        # memory counts cannot be reconstructed by summing daily aggregates.
        windows = ",".join("(%s,%s::timestamptz)" for _ in starts)
        window_params = tuple(
            value
            for length, boundary in starts.items()
            for value in (length, max(boundary, tracking))
        ) + (now,)
        window_join = (
            f" FROM (VALUES {windows}) AS periods(days,start_at) "
            "LEFT JOIN space.activity_events ON created_at >= start_at AND created_at <= %s "
        )
        summaries = {
            row.pop("days"): row
            for row in connection.execute(
                "SELECT days," + MEASURES + window_join + "GROUP BY days", window_params
            ).fetchall()
        }
        for row in connection.execute(
            "SELECT days,count(DISTINCT claim) AS n"
            + window_join
            + "LEFT JOIN LATERAL unnest(returned_claims) AS claim ON true GROUP BY days",
            window_params,
        ).fetchall():
            summaries[row["days"]]["reused_memories"] = row["n"]
        activity = connection.execute(
            "SELECT (created_at AT TIME ZONE 'UTC')::date AS date,"
            + MEASURES
            + where
            + "GROUP BY 1 ORDER BY 1",
            period,
        ).fetchall()
        agents = connection.execute(
            "SELECT days,agent_id,actor_type,max(created_at) AS last_active_at,"
            + MEASURES
            + window_join
            + "WHERE id IS NOT NULL GROUP BY days,agent_id,actor_type "
            "ORDER BY count(*) DESC,agent_id",
            window_params,
        ).fetchall()
        strategies = connection.execute(
            "SELECT days,strategy,"
            + MEASURES
            + window_join
            + "WHERE kind='retrieval' GROUP BY days,strategy ORDER BY strategy",
            window_params,
        ).fetchall()
        recent = connection.execute(
            "SELECT id,created_at,agent_id,actor_type,kind,resource_id,new_items,item_count,"
            "outcome,duration_ms,EXISTS(SELECT 1 FROM space.resources r "
            "WHERE r.id=activity_events.resource_id) AS resource_exists "
            "FROM space.activity_events WHERE created_at >= %s AND created_at <= %s "
            "ORDER BY created_at DESC,id DESC LIMIT 12",
            period,
        ).fetchall()
    by_day = {row["date"]: row for row in activity}
    series = []
    for offset in range(days):
        day = (start + timedelta(days=offset)).date()
        available = day >= tracking.astimezone(UTC).date()
        row = by_day.get(day, {})
        series.append(
            {
                "date": day.isoformat(),
                "available": available,
                **{
                    key: row.get(key, 0) if available else None
                    for key in (
                        "document_writes",
                        "memories_created",
                        "retrievals",
                    )
                },
            }
        )
    periods = {
        length: {
            "period_start": boundary,
            "summary": summaries[length],
            "agents": [
                {k: v for k, v in row.items() if k != "days"}
                for row in agents
                if row["days"] == length
            ],
            "strategies": [
                {k: v for k, v in row.items() if k != "days"}
                for row in strategies
                if row["days"] == length
            ],
        }
        for length, boundary in starts.items()
    }
    return {
        "space_id": str(space_id),
        "days": days,
        "as_of": now,
        "period_start": start,
        "tracking_started_at": tracking,
        "totals": totals,
        "summary": periods[days]["summary"],
        "activity": series,
        "agents": periods[days]["agents"],
        "strategies": periods[days]["strategies"],
        "recent": recent,
        "usage": quotas.usage(workspace, identity, space_id),
        **({"periods": periods} if all_periods else {}),
    }
