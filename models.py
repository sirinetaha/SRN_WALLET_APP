from db import get_db

DEMO_USER_ID = 1  # temporary until authentication exists


def get_categories(kind: str):
    """
    Returns merged list:
    - default categories (user_id IS NULL)
    - user categories (user_id = DEMO_USER_ID)
    """
    db = get_db()
    rows = db.execute(
        """
        SELECT id, user_id, name, kind, is_default
        FROM categories
        WHERE kind = ?
          AND (user_id IS NULL OR user_id = ?)
        ORDER BY is_default DESC, name ASC
        """,
        (kind, DEMO_USER_ID),
    ).fetchall()
    return rows


def add_custom_category(name: str, kind: str):
    db = get_db()
    db.execute(
        """
        INSERT INTO categories (user_id, name, kind, is_default)
        VALUES (?, ?, ?, 0)
        """,
        (DEMO_USER_ID, name.strip(), kind),
    )
    db.commit()
def get_currencies():
    db = get_db()
    return db.execute("SELECT code, symbol FROM currencies ORDER BY code").fetchall()


def add_transaction(user_id: int, category_id: int, amount_cents: int, currency_code: str, tx_date: str, note: str | None):
    db = get_db()
    db.execute(
        """
        INSERT INTO transactions (user_id, category_id, amount_cents, currency_code, tx_date, note)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (user_id, category_id, amount_cents, currency_code, tx_date, note),
    )
    db.commit()
def get_transactions_by_date(start_date: str, end_date: str):
    db = get_db()
    return db.execute(
        """
        SELECT
            t.id,
            t.tx_date,
            t.amount_cents,
            t.currency_code,
            t.note,
            c.name AS category_name,
            c.kind AS category_kind
        FROM transactions t
        JOIN categories c ON c.id = t.category_id
        WHERE t.user_id = ?
          AND t.tx_date BETWEEN ? AND ?
        ORDER BY t.tx_date DESC, t.id DESC
        """,
        (DEMO_USER_ID, start_date, end_date),
    ).fetchall()
def get_recent_transactions(limit: int = 5):
    db = get_db()
    return db.execute(
        """
        SELECT
            t.id,
            t.tx_date,
            t.amount_cents,
            t.currency_code,
            t.note,
            c.name AS category_name,
            c.kind AS category_kind
        FROM transactions t
        JOIN categories c ON c.id = t.category_id
        WHERE t.user_id = ?
        ORDER BY t.tx_date DESC, t.id DESC
        LIMIT ?
        """,
        (DEMO_USER_ID, limit),
    ).fetchall()
def get_category_totals(start_date: str, end_date: str):
    db = get_db()
    return db.execute(
        """
        SELECT
            c.id AS category_id,
            c.name AS category_name,
            c.kind AS category_kind,
            t.currency_code,
            SUM(t.amount_cents) AS total_cents
        FROM transactions t
        JOIN categories c ON c.id = t.category_id
        WHERE t.user_id = ?
          AND t.tx_date BETWEEN ? AND ?
        GROUP BY c.id, t.currency_code
        ORDER BY c.kind, total_cents DESC
        """,
        (DEMO_USER_ID, start_date, end_date),
    ).fetchall()
def upsert_category_target(category_id: int, currency_code: str, target_cents: int):
    db = get_db()
    db.execute(
        """
        INSERT INTO category_targets (category_id, currency_code, target_cents, reached_at)
        VALUES (?, ?, ?, NULL)
        ON CONFLICT(category_id, currency_code)
        DO UPDATE SET target_cents = excluded.target_cents
        """,
        (category_id, currency_code, target_cents),
    )
    db.commit()


def get_targets_for_kind(kind: str):
    db = get_db()
    return db.execute(
        """
        SELECT
            ct.category_id,
            ct.currency_code,
            ct.target_cents,
            ct.reached_at,
            c.name AS category_name,
            c.kind AS category_kind
        FROM category_targets ct
        JOIN categories c ON c.id = ct.category_id
        WHERE c.kind = ?
          AND (c.user_id IS NULL OR c.user_id = ?)
        ORDER BY c.name, ct.currency_code
        """,
        (kind, DEMO_USER_ID),
    ).fetchall()
def get_target_progress(start_date: str, end_date: str, kind: str):
    """
    Returns one row per (category_id, currency_code) target with current total + reached flag.
    Totals are computed from transactions in the date range.
    """
    db = get_db()
    return db.execute(
        """
        SELECT
            ct.category_id,
            ct.currency_code,
            ct.target_cents,
            ct.reached_at,
            c.name AS category_name,
            c.kind AS category_kind,
            COALESCE(SUM(t.amount_cents), 0) AS total_cents
        FROM category_targets ct
        JOIN categories c ON c.id = ct.category_id
        LEFT JOIN transactions t
          ON t.category_id = ct.category_id
         AND t.currency_code = ct.currency_code
         AND t.user_id = ?
         AND t.tx_date BETWEEN ? AND ?
        WHERE c.kind = ?
        GROUP BY ct.category_id, ct.currency_code
        ORDER BY (total_cents * 1.0 / ct.target_cents) DESC
        """,
        (DEMO_USER_ID, start_date, end_date, kind),
    ).fetchall()
def mark_target_reached(category_id: int, currency_code: str):
    db = get_db()
    db.execute(
        """
        UPDATE category_targets
        SET reached_at = datetime('now')
        WHERE category_id = ?
          AND currency_code = ?
          AND reached_at IS NULL
        """,
        (category_id, currency_code),
    )
    db.commit()
def get_active_category_cards(start_date: str, end_date: str, kind: str = "expense"):
    db = get_db()
    return db.execute(
        """
        SELECT
            c.id AS category_id,
            c.name AS category_name,
            c.kind AS category_kind,
            t.currency_code,
            SUM(t.amount_cents) AS total_cents,
            ct.target_cents,
            ct.reached_at
        FROM transactions t
        JOIN categories c ON c.id = t.category_id
        LEFT JOIN category_targets ct
          ON ct.category_id = c.id
         AND ct.currency_code = t.currency_code
        WHERE t.user_id = ?
          AND t.tx_date BETWEEN ? AND ?
          AND c.kind = ?
        GROUP BY c.id, t.currency_code
        HAVING total_cents > 0
        ORDER BY total_cents DESC
        """,
        (DEMO_USER_ID, start_date, end_date, kind),
    ).fetchall()
