"""Alembic environment: runs the migrations on the connection the app passes in.

The app never runs Alembic from the command line: `state/database.py` builds the config, puts
the open connection into `config.attributes` and calls `alembic.command.upgrade`.
"""

from alembic import context
from sqlalchemy import Connection

from transcribe_ui.state.constants import MIGRATION_CONNECTION_KEY
from transcribe_ui.state.models import Base


def run_migrations() -> None:
    """Run the pending migrations in batch mode (SQLite cannot ALTER most things in place)."""
    connection = context.config.attributes[MIGRATION_CONNECTION_KEY]
    if not isinstance(connection, Connection):
        msg = "The migrations need an open connection in the Alembic config."
        raise TypeError(msg)
    context.configure(connection=connection, target_metadata=Base.metadata, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


run_migrations()
