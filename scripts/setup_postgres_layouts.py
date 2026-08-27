"""Create persistent layout schemas and apply Diesel migrations to each one.

This command changes both PostgreSQL shards. It is intentionally separate from
``load_postgres.py --dry-run`` and must not be run as an offline validation step.
"""

import os
import subprocess

from dotenv import load_dotenv

LAYOUT_SCHEMAS = ("hash_layout", "industry_layout")
SHARD_DATABASE_URL_ENV = ("POSTGRES_SHARD_A_URL", "POSTGRES_SHARD_B_URL")


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


def main() -> None:
    """Prepare both layout schemas on both physical PostgreSQL shards."""
    load_dotenv()
    for db_url in SHARD_DATABASE_URL_ENV:
        database_url = os.getenv(db_url)
        if not database_url:
            raise RuntimeError(
                f"environment variable {db_url} is missing"
            )
        for schema in LAYOUT_SCHEMAS:
            create_schema(database_url, schema)
            run_diesel_migrations(database_url, schema)
            print(f"Prepared {schema} using {db_url}")


if __name__ == "__main__":
    main()
