from flask import Flask, render_template, request, redirect, url_for, flash
from datetime import date
from db import get_conn, init_db, seed_defaults_for_user
import io
import base64
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from datetime import date
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash
from flask import session
import os
import random
import smtplib
from email.message import EmailMessage
from datetime import datetime, timedelta, UTC

app = Flask(__name__)
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
)

app.secret_key = os.environ.get("SECRET_KEY", os.urandom(32))
init_db()

CURRENCIES = ["USD", "EUR", "TRY", "LBP"]
def generate_otp():
    # 6-digit numeric code
    return f"{random.randint(0, 999999):06d}"

def utc_now():
    return datetime.now(UTC)

def utc_plus_minutes(minutes: int):
    return (utc_now() + timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M:%S")

def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped

def current_user_id():
    return session.get("user_id")

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


def global_balances(conn,user_id):
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
@login_required
def home():
    uid = current_user_id()

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
    if not uid:
        return redirect(url_for("login"))

    with get_conn() as conn:
        cat = conn.execute(
            "SELECT * FROM categories WHERE id=? AND user_id=?",
            (category_id, uid)
        ).fetchone()

        if not cat:
            flash("Category not found.", "error")
            return redirect(url_for("home"))

        balances = category_balance(conn, category_id, uid)

    # Filter currencies with balance > 0 (ONLY for withdraw)
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
    if not uid:
        return redirect(url_for("login"))

    # amount parsing
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
        # Validate category exists
        cat = conn.execute("SELECT * FROM categories WHERE id=? AND user_id=?", (category_id,uid)).fetchone()
        if not cat:
            flash("Category not found.", "error")
            return redirect(url_for("home"))

        # Withdraw validation: cannot exceed category balance in that currency
        if tx_type == "withdraw":
            bals = category_balance(conn, category_id, uid)  # ✅ correct
            if amount > bals.get(currency, 0.0) + 1e-9:
                flash(f"Insufficient funds in {currency}. Available: {bals.get(currency, 0.0):.2f}", "error")
                return redirect(url_for("withdraw_form", category_id=category_id))

        uid = current_user_id()

        conn.execute("""
            INSERT INTO transactions(user_id, category_id, type, amount, currency, tx_date, note)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (uid, category_id, tx_type, amount, currency, tx_date, note if note else None))


    flash("Saved.", "success")
    return redirect(url_for("home"))

@app.get("/transactions")
@login_required
def transactions():
    uid = current_user_id()
    # optional filter by category or date range later
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
@login_required
def add_category_post():
    uid = current_user_id()
    raw = (request.form.get("name") or "")
    name = " ".join(raw.strip().split())  # trim + collapse multiple spaces

    if not name:
        flash("Category name is required.", "error")
        return redirect(url_for("add_category"))

    if len(name) > 40:
        flash("Category name is too long (max 40).", "error")
        return redirect(url_for("add_category"))

    with get_conn() as conn:
        # Case-insensitive duplicate check per user
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
@login_required
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

        # Delete category
        conn.execute(
            "DELETE FROM categories WHERE id=? AND user_id=?",
            (category_id, uid)
        )

    flash("Category deleted.", "success")
    return redirect(url_for("home"))
@app.get("/reports")
@login_required
def reports():
    uid = current_user_id()
    today = date.today()
    start_default = today.replace(day=1).isoformat()
    end_default = today.isoformat()

    start = request.args.get("from", start_default)
    end = request.args.get("to", end_default)

    # Currency filter: ALL or one of CURRENCIES
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
        # If no data, return None
        if income <= 0 and expense <= 0:
            return None

        fig = plt.figure(figsize=(6, 6), dpi=150)
        values = [income, expense]
        labels = ["Income", "Expenses"]

        # Donut (pie with hole)
        plt.pie(
            values,
            labels=labels,
            autopct=lambda pct: f"{pct:.1f}%" if sum(values) > 0 else "",
            startangle=90,
            wedgeprops={"width": 0.45}
        )
        plt.title(title)

        buf = io.BytesIO()
        plt.tight_layout()
        fig.savefig(buf, format="png")
        plt.close(fig)
        buf.seek(0)

        return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("utf-8")

    charts = {}  # currency -> data_url

    with get_conn() as conn:
        if selected_currency == "ALL":
            for cur in CURRENCIES:
                inc, exp = fetch_income_expense(conn, cur)
                url = donut_chart_data_url(f"Income vs Expenses ({cur})", inc, exp)
                if url:
                    charts[cur] = {
                        "income": inc,
                        "expense": exp,
                        "url": url
                    }
        else:
            inc, exp = fetch_income_expense(conn, selected_currency)
            url = donut_chart_data_url(f"Income vs Expenses ({selected_currency})", inc, exp)
            if url:
                charts[selected_currency] = {
                    "income": inc,
                    "expense": exp,
                    "url": url
                }

    return render_template(
        "reports.html",
        start=start,
        end=end,
        selected_currency=selected_currency,
        currencies=CURRENCIES,
        charts=charts
    )
@app.get("/register")
def register():
    return render_template("register.html")

@app.post("/register")
def register_post():
    email = (request.form.get("email") or "").strip().lower()
    password = request.form.get("password") or ""

    if not email or not password:
        flash("Email and password are required.", "error")
        return redirect(url_for("register"))

    if len(password) < 6:
        flash("Password must be at least 6 characters.", "error")
        return redirect(url_for("register"))

    pw_hash = generate_password_hash(password)

    # Create OTP + store hash (never store code in plain text)
    code = generate_otp()
    code_hash = generate_password_hash(code)
    expires_at = utc_plus_minutes(10)

    with get_conn() as conn:
        try:
            conn.execute("""
                INSERT INTO users(email, password_hash, is_verified, verify_code_hash, verify_expires_at)
                VALUES (?, ?, 0, ?, ?)
            """, (email, pw_hash, code_hash, expires_at))
        except Exception:
            flash("Email already registered.", "error")
            return redirect(url_for("register"))

        user = conn.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
        user_id = user["id"]

    # Send email AFTER DB write
    try:
        send_verification_email(email, code)
    except Exception as e:
        # Don’t leave user stuck; show clear message
        flash(f"Account created, but email could not be sent. Check SMTP settings. ({e})", "error")
        return redirect(url_for("login"))

    # Store "pending verification" in session
    session["pending_user_id"] = user_id
    flash("We sent a verification code to your email.", "success")
    return redirect(url_for("verify_email"))

@app.get("/verify")
def verify_email():
    pending = session.get("pending_user_id")
    if not pending:
        flash("No verification pending.", "error")
        return redirect(url_for("login"))
    return render_template("verify.html")

@app.post("/verify")
def verify_email_post():
    pending = session.get("pending_user_id")
    if not pending:
        flash("No verification pending.", "error")
        return redirect(url_for("login"))

    code = (request.form.get("code") or "").strip()

    with get_conn() as conn:
        user = conn.execute(
            "SELECT id, email, is_verified, verify_code_hash, verify_expires_at FROM users WHERE id=?",
            (pending,)
        ).fetchone()

        if not user:
            flash("User not found.", "error")
            return redirect(url_for("login"))

        if user["is_verified"] == 1:
            flash("Already verified.", "success")
            session.pop("pending_user_id", None)
            return redirect(url_for("login"))

        # Check expiry
        if not user["verify_expires_at"]:
            flash("No verification code found. Please request a new one.", "error")
            return redirect(url_for("verify_email"))

        expires = datetime.strptime(
            user["verify_expires_at"],
                "%Y-%m-%d %H:%M:%S"
                ).replace(tzinfo=UTC)

        if utc_now() > expires:
            flash("Code expired. Please request a new one.", "error")
            return redirect(url_for("verify_email"))


        # Check code hash
        if not user["verify_code_hash"] or not check_password_hash(user["verify_code_hash"], code):
            flash("Invalid code.", "error")
            return redirect(url_for("verify_email"))

        # Verified ✅
        conn.execute("""
            UPDATE users
            SET is_verified=1, verify_code_hash=NULL, verify_expires_at=NULL
            WHERE id=?
        """, (pending,))

    session.pop("pending_user_id", None)

    # Now log them in + seed categories
    session["user_id"] = pending
    with get_conn() as conn:
        seed_defaults_for_user(pending, conn)

    flash("Email verified. Welcome!", "success")
    return redirect(url_for("home"))

@app.get("/login")
def login():
    return render_template("login.html")

@app.post("/login")
def login_post():
    email = (request.form.get("email") or "").strip().lower()
    password = request.form.get("password") or ""

    if not email or not password:
        flash("Email and password are required.", "error")
        return redirect(url_for("login"))

    with get_conn() as conn:
        user = conn.execute(
            "SELECT * FROM users WHERE email=?",
            (email,)
        ).fetchone()

    # ✅ If user doesn't exist -> show message (no verification)
    if not user:
        flash("No account found for this email. Please create an account first.", "error")
        return redirect(url_for("register"))

    if not check_password_hash(user["password_hash"], password):
        flash("Invalid email or password.", "error")
        return redirect(url_for("login"))

    # ✅ If not verified -> redirect to verify page WITHOUT sending a new code
    if user["is_verified"] == 0:
        session["pending_user_id"] = user["id"]
        flash("Your account is not verified. Please enter the code sent to your email when you registered.", "error")
        return redirect(url_for("verify_email"))

    # ✅ Verified -> login
    session.clear()
    session["user_id"] = user["id"]
    flash("Welcome back.", "success")
    return redirect(url_for("home"))

    

@app.post("/logout")
def logout():
    session.clear()
    flash("Logged out.", "success")
    return redirect(url_for("login"))
def send_verification_email(to_email: str, code: str):
    smtp_host = os.environ.get("SMTP_HOST")
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = os.environ.get("SMTP_USER")
    smtp_pass = os.environ.get("SMTP_PASS")
    smtp_pass = (smtp_pass or "").replace(" ", "")
    from_email = os.environ.get("FROM_EMAIL", smtp_user)
    print("=== SMTP DEBUG ===")
    print("SMTP_HOST:", os.environ.get("SMTP_HOST"))
    print("SMTP_PORT:", os.environ.get("SMTP_PORT"))
    print("SMTP_USER:", os.environ.get("SMTP_USER"))
    print("FROM_EMAIL:", os.environ.get("FROM_EMAIL"))
    print("==================")
    print("SMTP_PASS present:", bool(os.environ.get("SMTP_PASS")), "len:", len(os.environ.get("SMTP_PASS") or ""))


    if not all([smtp_host, smtp_user, smtp_pass, from_email]):
        raise RuntimeError("SMTP env vars missing. Set SMTP_HOST, SMTP_USER, SMTP_PASS, FROM_EMAIL.")

    msg = EmailMessage()
    msg["Subject"] = "SRN Wallet verification code"
    msg["From"] = from_email
    msg["To"] = to_email
    msg.set_content(
        f"Your SRN Wallet verification code is: {code}\n\n"
        f"This code expires in 10 minutes.\n"
        f"If you didn't request this, ignore this email."
    )

    with smtplib.SMTP(smtp_host, smtp_port) as server:
        server.starttls()
        server.login(smtp_user, smtp_pass)
        server.send_message(msg)


if __name__ == "__main__":
    
    app.run(host="0.0.0.0",port=5000, debug=True)

