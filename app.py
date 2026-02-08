# app.py — Single-user SRN Envelope Wallet (no login / no register / no email)
# Opens directly to Home and always uses one local user in the DB.

from flask import Flask, render_template, request, redirect, url_for, flash, session
from datetime import date
from functools import wraps
import os
import io
import base64
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from db import get_conn, init_db, seed_defaults_for_user


app = Flask(__name__)
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
)

# Keep a stable secret key in Render ENV for persistent sessions across deploys
# (recommended) SECRET_KEY="some-long-random-string"
app.secret_key = os.environ.get("SECRET_KEY", os.urandom(32))

init_db()

CURRENCIES = ["USD", "EUR", "TRY", "LBP"]

# Single-user identity (only used to find/create your one user row)
SINGLE_USER_EMAIL = os.environ.get("SINGLE_USER_EMAIL", "sirine@local")


def current_user_id():
    return session.get("user_id")


def login_required(view):
    """Kept for safety; in single-user mode user_id is always present."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            # Should never happen because of before_request
            session["user_id"] = ensure_single_user()
        return view(*args, **kwargs)
    return wrapped


def _users_table_columns(conn):
    # Works with SQLite
    rows = conn.execute("PRAGMA table_info(users)").fetchall()
    # row may be dict-like depending on row_factory; handle both
    cols = []
    for r in rows:
        if isinstance(r, dict):
            cols.append(r.get("name"))
        else:
            # PRAGMA table_info: cid, name, type, notnull, dflt_value, pk
            cols.append(r[1])
    return set([c for c in cols if c])


def ensure_single_user():
    """
    Ensures there is exactly one local user and returns its user_id.
    Creates user row if missing and seeds default categories for that user.
    This function is schema-tolerant: it adapts to your users table columns.
    """
    with get_conn() as conn:
        u = conn.execute("SELECT id FROM users WHERE email=?", (SINGLE_USER_EMAIL,)).fetchone()
        if u:
            return int(u["id"]) if isinstance(u, dict) or hasattr(u, "__getitem__") else int(u[0])

        cols = _users_table_columns(conn)

        # Build a safe INSERT that matches your actual schema
        insert_cols = []
        insert_vals = []

        if "email" in cols:
            insert_cols.append("email")
            insert_vals.append(SINGLE_USER_EMAIL)

        # Optional columns often present in your earlier code
        if "is_verified" in cols:
            insert_cols.append("is_verified")
            insert_vals.append(1)

        if "password_hash" in cols:
            insert_cols.append("password_hash")
            insert_vals.append("")  # not used in single-user mode

        # If your schema has created_at, etc., rely on defaults; do not invent values.

        if not insert_cols:
            # Extremely unlikely, but prevents silent breakage
            raise RuntimeError("Could not detect usable columns in users table (expected at least 'email').")

        sql = f"INSERT INTO users({', '.join(insert_cols)}) VALUES ({', '.join(['?'] * len(insert_cols))})"
        cur = conn.execute(sql, tuple(insert_vals))
        user_id = cur.lastrowid

    # Seed defaults (categories) for this user
    seed_defaults_for_user(user_id)
    return int(user_id)


@app.before_request
def auto_login_single_user():
    # Always keep a user_id in session so app opens to home without auth.
    if "user_id" not in session:
        session["user_id"] = ensure_single_user()


def category_balance(conn, category_id: int, user_id: int):
    rows = conn.execute("""
        SELECT currency,
               SUM(CASE WHEN type='deposit' THEN amount ELSE -amount END) AS bal
        FROM transactions
        WHERE category_id=? AND user_id=?
        GROUP BY currency
    """, (category_id, user_id)).fetchall()

    balances = {c: 0.0 for c in CURRENCIES}
    for r in rows:
        balances[r["currency"]] = float(r["bal"] or 0.0)
    return balances


def global_balances(conn, user_id: int):
    rows = conn.execute("""
        SELECT currency,
               SUM(CASE WHEN type='deposit' THEN amount ELSE -amount END) AS bal
        FROM transactions
        WHERE user_id=?
        GROUP BY currency
    """, (user_id,)).fetchall()

    balances = {c: 0.0 for c in CURRENCIES}
    for r in rows:
        balances[r["currency"]] = float(r["bal"] or 0.0)
    return balances


@app.get("/")
def home():
    uid = current_user_id()
    if not uid:
        uid = ensure_single_user()
        session["user_id"] = uid

    with get_conn() as conn:
        cats = conn.execute("""
            SELECT * FROM categories
            WHERE user_id=?
            ORDER BY is_default DESC, name ASC
        """, (uid,)).fetchall()

        cat_cards = []
        for c in cats:
            bals = category_balance(conn, c["id"], uid)

            primary_currency = next(
                (cur for cur in CURRENCIES if abs(bals[cur]) > 1e-9),
                "USD"
            )

            cat_cards.append({
                "id": c["id"],
                "name": c["name"],
                "balances": bals,
                "primary_currency": primary_currency,
                "primary_value": bals[primary_currency],
                "is_default": c["is_default"],
            })

        g = global_balances(conn, uid)

    return render_template("home.html", global_balances=g, cat_cards=cat_cards)


@app.get("/category/<int:category_id>/deposit")
def deposit_form(category_id):
    return tx_form(category_id, tx_type="deposit")


@app.get("/category/<int:category_id>/withdraw")
def withdraw_form(category_id):
    return tx_form(category_id, tx_type="withdraw")


def tx_form(category_id: int, tx_type: str):
    uid = current_user_id()

    with get_conn() as conn:
        cat = conn.execute(
            "SELECT * FROM categories WHERE id=? AND user_id=?",
            (category_id, uid)
        ).fetchone()

        if not cat:
            flash("Category not found.", "error")
            return redirect(url_for("home"))

        balances = category_balance(conn, category_id, uid)

    # Withdraw: show only currencies with positive balance
    if tx_type == "withdraw":
        available_currencies = [cur for cur, bal in balances.items() if bal > 0]
        if not available_currencies:
            flash("No available balance to withdraw in this category.", "error")
            return redirect(url_for("home"))
    else:
        available_currencies = CURRENCIES

    return render_template(
        "tx_form.html",
        cat=cat,
        tx_type=tx_type,
        currencies=available_currencies,
        today=date.today().isoformat(),
        balances=balances
    )


@app.post("/category/<int:category_id>/save")
def save_tx(category_id):
    tx_type = request.form.get("tx_type")  # deposit / withdraw
    currency = request.form.get("currency")
    tx_date = request.form.get("tx_date") or date.today().isoformat()
    note = (request.form.get("note") or "").strip()
    uid = current_user_id()

    raw_amount = (request.form.get("amount") or "").strip()
    try:
        amount = float(raw_amount)
    except ValueError:
        flash("Amount must be a number.", "error")
        return redirect(request.referrer or url_for("home"))

    if amount <= 0:
        flash("Amount must be greater than 0.", "error")
        return redirect(request.referrer or url_for("home"))

    if tx_type not in ("deposit", "withdraw"):
        flash("Invalid transaction type.", "error")
        return redirect(url_for("home"))

    if currency not in CURRENCIES:
        flash("Invalid currency.", "error")
        return redirect(request.referrer or url_for("home"))

    with get_conn() as conn:
        cat = conn.execute(
            "SELECT * FROM categories WHERE id=? AND user_id=?",
            (category_id, uid)
        ).fetchone()
        if not cat:
            flash("Category not found.", "error")
            return redirect(url_for("home"))

        if tx_type == "withdraw":
            bals = category_balance(conn, category_id, uid)
            if amount > (bals.get(currency, 0.0) + 1e-9):
                flash(f"Insufficient funds in {currency}. Available: {bals.get(currency, 0.0):.2f}", "error")
                return redirect(url_for("withdraw_form", category_id=category_id))

        conn.execute("""
            INSERT INTO transactions(user_id, category_id, type, amount, currency, tx_date, note)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (uid, category_id, tx_type, amount, currency, tx_date, note if note else None))

    flash("Saved.", "success")
    return redirect(url_for("home"))


@app.get("/transactions")
def transactions():
    uid = current_user_id()
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT t.*, c.name AS category_name
            FROM transactions t
            JOIN categories c ON c.id = t.category_id
            WHERE t.user_id=?
            ORDER BY t.tx_date DESC, t.id DESC
            LIMIT 200
        """, (uid,)).fetchall()
    return render_template("transactions.html", rows=rows)


@app.get("/categories/new")
def add_category():
    return render_template("add_category.html")


@app.post("/categories/new")
def add_category_post():
    uid = current_user_id()
    raw = (request.form.get("name") or "")
    name = " ".join(raw.strip().split())

    if not name:
        flash("Category name is required.", "error")
        return redirect(url_for("add_category"))

    if len(name) > 40:
        flash("Category name is too long (max 40).", "error")
        return redirect(url_for("add_category"))

    with get_conn() as conn:
        existing = conn.execute(
            "SELECT 1 FROM categories WHERE user_id=? AND lower(name)=lower(?)",
            (uid, name)
        ).fetchone()
        if existing:
            flash("This category already exists.", "error")
            return redirect(url_for("add_category"))

        conn.execute(
            "INSERT INTO categories(user_id, name, is_default) VALUES (?, ?, 0)",
            (uid, name)
        )

    flash("Category added.", "success")
    return redirect(url_for("home"))


@app.post("/category/<int:category_id>/delete")
def delete_category(category_id):
    uid = current_user_id()

    with get_conn() as conn:
        cat = conn.execute(
            "SELECT * FROM categories WHERE id=? AND user_id=?",
            (category_id, uid)
        ).fetchone()

        if not cat:
            flash("Category not found.", "error")
            return redirect(url_for("home"))

        conn.execute(
            "DELETE FROM categories WHERE id=? AND user_id=?",
            (category_id, uid)
        )

    flash("Category deleted.", "success")
    return redirect(url_for("home"))


@app.get("/reports")
def reports():
    uid = current_user_id()
    today = date.today()
    start_default = today.replace(day=1).isoformat()
    end_default = today.isoformat()

    start = request.args.get("from", start_default)
    end = request.args.get("to", end_default)

    selected_currency = request.args.get("currency", "ALL").upper()
    if selected_currency != "ALL" and selected_currency not in CURRENCIES:
        selected_currency = "ALL"

    def fetch_income_expense(conn, currency: str):
        rows = conn.execute("""
            SELECT type, SUM(amount) AS total
            FROM transactions
            WHERE user_id = ?
              AND tx_date >= ?
              AND tx_date <= ?
              AND currency = ?
            GROUP BY type
        """, (uid, start, end, currency)).fetchall()

        income = 0.0
        expense = 0.0
        for r in rows:
            t = r["type"]
            total = float(r["total"] or 0.0)
            if t == "deposit":
                income = total
            elif t == "withdraw":
                expense = total
        return income, expense

    def donut_chart_data_url(title: str, income: float, expense: float):
        if income <= 0 and expense <= 0:
            return None

        fig = plt.figure(figsize=(6, 6), dpi=150)
        values = [income, expense]
        labels = ["Income", "Expenses"]

        plt.pie(
            values,
            labels=labels,
            autopct=lambda pct: f"{pct:.1f}%" if sum(values) > 0 else "",
            startangle=90,
            wedgeprops={"width": 0.45},
        )
        plt.title(title)

        buf = io.BytesIO()
        plt.tight_layout()
        fig.savefig(buf, format="png")
        plt.close(fig)
        buf.seek(0)

        return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("utf-8")

    charts = {}

    with get_conn() as conn:
        if selected_currency == "ALL":
            for cur in CURRENCIES:
                inc, exp = fetch_income_expense(conn, cur)
                url = donut_chart_data_url(f"Income vs Expenses ({cur})", inc, exp)
                if url:
                    charts[cur] = {"income": inc, "expense": exp, "url": url}
        else:
            inc, exp = fetch_income_expense(conn, selected_currency)
            url = donut_chart_data_url(f"Income vs Expenses ({selected_currency})", inc, exp)
            if url:
                charts[selected_currency] = {"income": inc, "expense": exp, "url": url}

    return render_template(
        "reports.html",
        start=start,
        end=end,
        selected_currency=selected_currency,
        currencies=CURRENCIES,
        charts=charts,
    )



if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
