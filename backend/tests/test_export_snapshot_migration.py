from pathlib import Path

from test_database_setup import database as database_fixture

database = database_fixture

MIGRATION_SQL = Path(__file__).parents[1] / "migrations/versions/0038_export_snapshots.sql"


def test_export_snapshot_migration_applies_alone_and_again(database):
    """An upgrade runs 0038 in its own transaction, without 0003's search_path."""
    connect, engine, _, _, _ = database
    with engine.begin() as connection:
        for statement in (
            "DROP TRIGGER retire_claim_vector ON space.memory_claims",
            "DROP FUNCTION space.retire_claim_vector()",
            "DROP TABLE space.retired_claim_vectors",
        ):
            connection.exec_driver_sql(statement)
    migration = MIGRATION_SQL.read_text()
    for _ in range(2):
        with engine.begin() as connection:
            connection.exec_driver_sql(migration, execution_options={"no_parameters": True})
    with connect() as connection:
        assert connection.execute(
            "SELECT format_type(atttypid, atttypmod) FROM pg_attribute "
            "WHERE attrelid='space.retired_claim_vectors'::regclass AND attname='embedding'"
        ).fetchone() == ("space.vector",)
