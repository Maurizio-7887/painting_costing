"""
cleanup_db.py — One-time database cleanup script.

Drops all tables created by the legacy db.create_all() approach so that
Flask-Migrate can build a clean schema from scratch via `flask db upgrade`.

Tables are dropped in foreign-key-safe order.
"""

import os
import sys

import psycopg2
from psycopg2 import sql

DATABASE_URL = os.environ.get("DATABASE_URL", "")

if not DATABASE_URL:
    print("ERROR: DATABASE_URL environment variable is not set.")
    sys.exit(1)

# Normalise legacy postgres:// scheme
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# Tables to drop, ordered to satisfy foreign-key constraints
TABLES = [
    "componente_macchina",
    "macchina_commessa",
    "kb_regola",
    "storico_costo",
    "lotto_item",
    "lotto",
    "ordine_ordine",
    "prodotto",
    "configurazione",
    "alembic_version",
]

def main():
    print("Connecting to database…")
    try:
        conn = psycopg2.connect(DATABASE_URL)
        conn.autocommit = True
    except Exception as exc:
        print(f"ERROR: Could not connect to database: {exc}")
        sys.exit(1)

    cursor = conn.cursor()

    for table in TABLES:
        # Check whether the table exists before attempting to drop it
        cursor.execute(
            """
            SELECT EXISTS (
                SELECT 1 FROM information_schema.tables
                WHERE table_schema = 'public'
                AND   table_name   = %s
            )
            """,
            (table,),
        )
        exists = cursor.fetchone()[0]

        if exists:
            cursor.execute(
                sql.SQL("DROP TABLE IF EXISTS {} CASCADE").format(
                    sql.Identifier(table)
                )
            )
            print(f"  ✓ Dropped table: {table}")
        else:
            print(f"  – Skipped (not found): {table}")

    cursor.close()
    conn.close()
    print("Database cleanup complete. Ready for `flask db upgrade`.")

if __name__ == "__main__":
    main()
