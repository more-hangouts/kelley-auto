import importlib
import re
import sys
from pathlib import Path

from sqlalchemy import text

from database.connection import engine

MIGRATIONS_DIR = Path(__file__).resolve().parent
MIGRATION_PATTERN = re.compile(r"^(\d{3})_[a-z0-9_]+\.py$")


def _ensure_tracking_table(conn) -> None:
    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                migration_id VARCHAR PRIMARY KEY,
                applied_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
    )


def _discover_migrations() -> list[tuple[str, Path]]:
    found = []
    for path in MIGRATIONS_DIR.iterdir():
        if not path.is_file():
            continue
        m = MIGRATION_PATTERN.match(path.name)
        if not m:
            continue
        migration_id = path.stem
        found.append((migration_id, path))
    found.sort(key=lambda x: x[0])
    return found


def _applied_ids(conn) -> set[str]:
    rows = conn.execute(text("SELECT migration_id FROM schema_migrations")).all()
    return {row[0] for row in rows}


def run() -> int:
    migrations = _discover_migrations()

    with engine.begin() as conn:
        _ensure_tracking_table(conn)
        applied = _applied_ids(conn)

    pending = [m for m in migrations if m[0] not in applied]

    if not pending:
        print(f"no pending migrations ({len(migrations)} already applied)")
        return 0

    for migration_id, path in migrations:
        if migration_id in applied:
            print(f"skipping {migration_id} (already applied)")
            continue

        module_name = f"database.migrations.{migration_id}"
        print(f"applying {migration_id}...", end=" ", flush=True)
        try:
            module = importlib.import_module(module_name)
            with engine.begin() as conn:
                module.upgrade(conn)
                conn.execute(
                    text("INSERT INTO schema_migrations (migration_id) VALUES (:id)"),
                    {"id": migration_id},
                )
            print("ok")
        except Exception as exc:
            print("FAILED")
            print(f"ERROR: {migration_id} failed: {exc}", file=sys.stderr)
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(run())
