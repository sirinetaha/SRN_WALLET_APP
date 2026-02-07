import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).with_name("srn_wallet.sqlite3")

def get_conn():
    conn = sqlite3.connect(DB_PATH, timeout=10)  # wait up to 10s
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=5000;")  # 5 seconds
    return conn


def init_db():
    from pathlib import Path
    schema = Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")
    with get_conn() as conn:
        conn.executescript(schema)

def seed_defaults_for_user(user_id: int, conn=None):
    defaults = ["Health", "Shopping", "Transportation", "Car Accessories", "Entertainment", "Personal"]

    if conn is None:
        with get_conn() as conn2:
            for name in defaults:
                conn2.execute(
                    "INSERT OR IGNORE INTO categories(user_id, name, is_default) VALUES (?, ?, 1)",
                    (user_id, name)
                )
        return

    # use the provided connection
    for name in defaults:
        conn.execute(
            "INSERT OR IGNORE INTO categories(user_id, name, is_default) VALUES (?, ?, 1)",
            (user_id, name)
        )

