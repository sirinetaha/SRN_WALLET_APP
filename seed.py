from db import get_db

def seed_initial_data():
    db = get_db()

    # 1) Demo user (temporary)
    db.execute("INSERT OR IGNORE INTO users (id, name) VALUES (1, ?)", ("Sirine",))

    # 2) Currencies
    currencies = [
        ("USD", "$"),
        ("TRY", "₺"),
        ("EUR", "€"),
        ("LBP","LBP")
    ]
    for code, symbol in currencies:
        db.execute(
            "INSERT OR IGNORE INTO currencies (code, symbol) VALUES (?, ?)",
            (code, symbol),
        )

    # 3) Default categories (user_id NULL, is_default=1)
    # We'll store 3 columns for names (simple approach for now)
    # So we need to modify schema first -> we’ll do it here cleanly:
    # BUT since schema already exists, we will keep names as English for Milestone 2,
    # and handle i18n labels in Milestone 10 without changing DB yet.

    default_expense = ["Food", "Transport", "Rent", "Shopping", "Health", "Bills", "Other"]
    default_income = ["Salary", "Freelance", "Gift", "Other Income"]

    for name in default_expense:
        db.execute(
            """
            INSERT INTO categories (user_id, name, kind, is_default)
            SELECT NULL, ?, 'expense', 1
            WHERE NOT EXISTS (
              SELECT 1 FROM categories WHERE user_id IS NULL AND name = ? AND kind='expense'
            )
            """,
            (name, name),
        )

    for name in default_income:
        db.execute(
            """
            INSERT INTO categories (user_id, name, kind, is_default)
            SELECT NULL, ?, 'income', 1
            WHERE NOT EXISTS (
              SELECT 1 FROM categories WHERE user_id IS NULL AND name = ? AND kind='income'
            )
            """,
            (name, name),
        )

    db.commit()
