"""Create persistent layout schemas and apply Diesel migrations to each one.

This command changes both PostgreSQL shards. It is intentionally separate from
``load_postgres.py --dry-run`` and must not be run as an offline validation step.
"""

import os
import subprocess

from dotenv import load_dotenv

LAYOUT_SCHEMAS = ("hash_layout", "industry_layout")
DATABASE_URL_ENVIRONMENTS = (
    "POSTGRES_SHARD_A_URL",
    "POSTGRES_SHARD_B_URL",
)


def create_schema(database_url: str, schema: str) -> None:
    """Create one fixed layout schema before Diesel targets it."""
    try:
        import psycopg
        from psycopg import sql
    except ImportError as error:
        raise RuntimeError(
            "psycopg is required; install scripts/requirements.txt"
        ) from error

    with psycopg.connect(database_url, autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(
                    sql.Identifier(schema)
                )
            )


def run_diesel_migrations(database_url: str, schema: str) -> None:
    """Run Diesel with its migration history isolated inside one schema."""
    migration_environment = os.environ.copy()
    migration_environment["DATABASE_URL"] = database_url
    migration_environment["PGOPTIONS"] = f"-c search_path={schema}"
    subprocess.run(
        ["diesel", "migration", "run"],
        check=True,
        env=migration_environment,
    )


def read_database_urls() -> dict[str, str]:
    """Read both required shard URLs without exposing their values."""
    database_urls = {}
    for environment_name in DATABASE_URL_ENVIRONMENTS:
        database_url = os.getenv(environment_name)
        if not database_url:
            raise RuntimeError(
                f"environment variable {environment_name} is missing"
            )
        database_urls[environment_name] = database_url
    return database_urls


def initiate_relational_schemas(database_urls: dict[str, str]) -> None:
    """Create and migrate both layouts on both physical shards."""
    for environment_name, database_url in database_urls.items():
        for schema in LAYOUT_SCHEMAS:
            create_schema(database_url, schema)
            run_diesel_migrations(database_url, schema)
            print(f"Prepared {schema} using {environment_name}")


def main() -> None:
    """Prepare both layout schemas on both physical PostgreSQL shards."""
    load_dotenv()
    initiate_relational_schemas(read_database_urls())


if __name__ == "__main__":
    main()
