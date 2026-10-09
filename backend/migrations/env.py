"""Use the transaction supplied by the setup command; never put credentials in an INI file."""

from alembic import context

connection = context.config.attributes.get("connection")
if connection is None:
    raise RuntimeError("Run migrations through python -m space.install")

context.configure(
    connection=connection,
    version_table="alembic_version",
    version_table_schema="space",
    transactional_ddl=True,
)
with context.begin_transaction():
    context.run_migrations()
