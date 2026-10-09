"""The installer's embedding-width rules, without a database."""

import pytest

from space.database_setup import DatabaseSetupError, ensure_embedding_width


class Result:
    def __init__(self, value):
        self.value = value

    def scalar_one(self):
        return self.value


class Connection:
    """Answers the width and row-count queries; records every statement."""

    def __init__(self, width, rows):
        self.width, self.rows, self.statements = width, rows, []

    def exec_driver_sql(self, statement):
        self.statements.append(statement)
        if "atttypmod" in statement:
            return Result(self.width)
        if "EXISTS" in statement:
            return Result(self.rows)
        return Result(None)


def test_a_matching_width_changes_nothing():
    connection = Connection(1536, rows=True)
    ensure_embedding_width(connection, 1536)
    assert len(connection.statements) == 1


def test_an_index_with_embeddings_keeps_its_width():
    connection = Connection(1536, rows=True)
    with pytest.raises(DatabaseSetupError, match="SPACE_MEMORY_EMBEDDING_DIMENSIONS=1536"):
        ensure_embedding_width(connection, 768)
    assert not any("ALTER TABLE" in statement for statement in connection.statements)


def test_an_empty_index_is_rebuilt_at_the_new_width():
    connection = Connection(1536, rows=False)
    ensure_embedding_width(connection, 1024)
    assert "space.vector(1024)" in connection.statements[-2]
    assert connection.statements[-1].endswith("USING hnsw (embedding space.vector_cosine_ops)")


@pytest.mark.parametrize("width", [0, 15, 2001, True, "768"])
def test_widths_outside_what_hnsw_indexes_are_refused(width):
    with pytest.raises(DatabaseSetupError, match="between 16 and 2000"):
        ensure_embedding_width(Connection(1536, rows=False), width)
