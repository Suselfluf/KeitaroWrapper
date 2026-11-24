from logging.config import fileConfig

from alembic import context

from app.config import database_url_from_env
from app.storage import Base, create_storage_engine

if context.config.config_file_name is not None:
    fileConfig(context.config.config_file_name)

database_url = database_url_from_env()

if context.is_offline_mode():
    context.configure(url=database_url, target_metadata=Base.metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = create_storage_engine(database_url)
    try:
        with engine.connect() as connection:
            context.configure(connection=connection, target_metadata=Base.metadata)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()
