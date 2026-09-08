"""Alembic environment.

Imports the project's own ``Settings`` and declarative ``Base`` rather
than duplicating database configuration or defining a second metadata
object — see BACKEND-03 §8. ``sqlalchemy.url`` is intentionally left
blank in ``alembic.ini``; it is always resolved here, at runtime, from
``Settings().DATABASE_URL`` (which itself just reads ``DATABASE_URL``
from the environment / ``.env``, exactly like the FastAPI app does).
"""

from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context

# Import every module that defines a model, so its table registers on
# `Base.metadata` before Alembic looks at it. Each import is for its
# side effect only (table registration); add one line here whenever a
# future stage introduces a new model module.
from app.audit import models as audit_models  # noqa: F401
from app.auth import models as auth_models  # noqa: F401
from app.campaigns import models as campaign_models  # noqa: F401
from app.core.config import get_settings
from app.orchestration import models as orchestration_models  # noqa: F401
from app.persistence import probe  # noqa: F401  (BACKEND-03: explicitly non-domain persistence probe)
from app.persistence.base import Base
from app.research import models as research_models  # noqa: F401
from app.strategy import models as strategy_models  # noqa: F401
from app.users import models as user_models  # noqa: F401
from app.workspaces import models as workspace_models  # noqa: F401

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _database_url() -> str:
    settings = get_settings()
    if not settings.DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL is not set. Alembic needs a database to migrate — "
            "set DATABASE_URL (e.g. via .env or the environment) before "
            "running `alembic upgrade`/`alembic revision --autogenerate`."
        )
    return settings.DATABASE_URL


def run_migrations_offline() -> None:
    """Emits SQL to stdout without opening a real connection."""
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = _database_url()
    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
