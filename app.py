"""
Expense Tracker - Flask + SQLite

Local run:
    pip install flask
    python app.py

Deploy karte waqt environment variables set karo:
    SECRET_KEY=<koi lambi random string>
    FLASK_DEBUG=0
"""

import calendar
import csv
import io
import math
import os
import re
import sqlite3
from datetime import datetime
from functools import wraps

from flask import (
    Flask,
    Response,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-only-secret-change-me")
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    MAX_CONTENT_LENGTH=5 * 1024 * 1024,  # 5MB: uploads (CSV import) iske andar hi rehni chahiye
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE = os.path.join(BASE_DIR, "database.db")

CATEGORIES = [
    "Food", "Travel", "Rent", "Shopping", "Bills",
    "Entertainment", "Health", "Education", "Other",
]
MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
YEAR_RE = re.compile(r"^\d{4}$")

SECURITY_QUESTIONS = [
    "What is your favourite food?",
    "What is the name of your first pet?",
    "What is your hometown?",
    "What was the name of your first school?",
]

EXPORT_FORMATS = {"csv", "xlsx", "pdf"}
IMPORT_MAX_ROWS = 2000       # ek baar mein import hone wali valid rows ki limit
IMPORT_MAX_ERRORS = 50       # response mein dikhaye jaane wale row-errors ki limit
IMPORT_ALLOWED_EXT = {".csv"}


# ---------- DATABASE ----------
def get_db():
    """Har request ke liye ek hi connection, request khatam hone par auto-close."""
    if "db" not in g:
        g.db = sqlite3.connect(DATABASE)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    conn = sqlite3.connect(DATABASE)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL COLLATE NOCASE,
            password TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS expenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            amount REAL NOT NULL,
            category TEXT NOT NULL,
            date TEXT NOT NULL,
            note TEXT,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS budgets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            category TEXT NOT NULL,
            monthly_limit REAL NOT NULL,
            UNIQUE (user_id, category),
            FOREIGN KEY (user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS salaries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            month TEXT NOT NULL,            -- 'YYYY-MM'
            amount REAL,                    -- NULL jab user ne skip kiya ho
            skipped INTEGER NOT NULL DEFAULT 0,
            UNIQUE (user_id, month),
            FOREIGN KEY (user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS incomes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            amount REAL NOT NULL,
            source TEXT NOT NULL,
            date TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );

        CREATE INDEX IF NOT EXISTS idx_expenses_user_date
            ON expenses (user_id, date);
        CREATE INDEX IF NOT EXISTS idx_incomes_user_date
            ON incomes (user_id, date);
        """
    )
    # Purane users table mein ye columns nahi hote; ALTER TABLE se add karo (data safe rehta hai).
    existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
    if "security_question" not in existing_cols:
        conn.execute("ALTER TABLE users ADD COLUMN security_question TEXT")
    if "security_answer" not in existing_cols:
        conn.execute("ALTER TABLE users ADD COLUMN security_answer TEXT")
    conn.commit()
    conn.close()


def normalize_answer(answer):
    """Security answer case aur extra spaces ignore karke compare hoti hai."""
    return re.sub(r"\s+", " ", str(answer).strip().lower())


# ---------- HELPERS ----------
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            if request.path.startswith("/api/"):
                return jsonify({"error": "Login required"}), 401
            return redirect(url_for("login"))
        return f(*args, **kwargs)

    return decorated


def parse_amount(value):
    """Valid positive number return karta hai, warna None."""
    try:
        amount = round(float(value), 2)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(amount) or amount <= 0:
        return None
    return amount


def validate_expense(data):
    """(cleaned_data, error_message) return karta hai."""
    data = data or {}

    amount = parse_amount(data.get("amount"))
    if amount is None:
        return None, "Amount 0 se bada valid number hona chahiye"

    category = str(data.get("category", "")).strip()
    if category not in CATEGORIES:
        return None, "Invalid category"

    date = str(data.get("date", "")).strip()
    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        return None, "Date YYYY-MM-DD format mein honi chahiye"

    note = str(data.get("note", "") or "").strip()[:200]

    return {"amount": amount, "category": category, "date": date, "note": note}, None


# ---------- PAGE ROUTES ----------
@app.route("/")
@login_required
def index():
    return render_template(
        "index.html",
        username=session.get("username"),
        categories=CATEGORIES,
    )


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        question = request.form.get("security_question", "").strip()
        answer = request.form.get("security_answer", "").strip()
        form_kwargs = {
            "error": None, "username": username, "question": question,
            "questions": SECURITY_QUESTIONS,
        }

        if not 3 <= len(username) <= 30:
            form_kwargs["error"] = "Username 3 se 30 characters ka hona chahiye"
            return render_template("register.html", **form_kwargs), 400
        if len(password) < 6:
            form_kwargs["error"] = "Password kam se kam 6 characters ka rakho"
            return render_template("register.html", **form_kwargs), 400
        if question not in SECURITY_QUESTIONS:
            form_kwargs["error"] = "Ek security question chuno, password bhoolne par kaam aayegi"
            return render_template("register.html", **form_kwargs), 400
        if len(answer) < 2:
            form_kwargs["error"] = "Security answer kam se kam 2 characters ka rakho"
            return render_template("register.html", **form_kwargs), 400

        db = get_db()
        try:
            db.execute(
                "INSERT INTO users (username, password, security_question, security_answer) "
                "VALUES (?, ?, ?, ?)",
                (
                    username,
                    generate_password_hash(password),
                    question,
                    generate_password_hash(normalize_answer(answer)),
                ),
            )
            db.commit()
        except sqlite3.IntegrityError:
            form_kwargs["error"] = "Ye username pehle se le liya gaya hai"
            return render_template("register.html", **form_kwargs), 409

        return redirect(url_for("login"))

    return render_template(
        "register.html", error=None, username="", question="", questions=SECURITY_QUESTIONS
    )


@app.route("/login", methods=["GET", "POST"])
def login():
    if "user_id" in session:
        return redirect(url_for("index"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        user = get_db().execute(
            "SELECT id, username, password FROM users WHERE username = ?",
            (username,),
        ).fetchone()

        if user and check_password_hash(user["password"], password):
            session.clear()
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            return redirect(url_for("index"))

        return render_template(
            "login.html", error="Username ya password galat hai", notice=None
        ), 401

    return render_template("login.html", error=None, notice=None)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    """Stage 1: username poochta hai. Stage 2: security question ka jawab + naya password."""
    if request.method == "POST":
        username = request.form.get("username", "").strip()

        if "answer" not in request.form:
            user = get_db().execute(
                "SELECT username, security_question FROM users WHERE username = ?",
                (username,),
            ).fetchone()
            if not user or not user["security_question"]:
                return render_template(
                    "forgot_password.html", stage=1,
                    error="Username nahi mila, ya iske liye security question set nahi hai.",
                ), 404
            return render_template(
                "forgot_password.html", stage=2,
                username=user["username"], question=user["security_question"], error=None,
            )

        # Stage 2 submit
        answer = request.form.get("answer", "")
        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")

        user = get_db().execute(
            "SELECT id, security_question, security_answer FROM users WHERE username = ?",
            (username,),
        ).fetchone()
        if not user or not user["security_answer"]:
            return render_template(
                "forgot_password.html", stage=1,
                error="Kuch gadbad ho gayi, dobara shuru karo.",
            ), 400

        stage2_kwargs = {"stage": 2, "username": username, "question": user["security_question"]}
        if not check_password_hash(user["security_answer"], normalize_answer(answer)):
            return render_template(
                "forgot_password.html", error="Answer sahi nahi hai.", **stage2_kwargs
            ), 401
        if len(new_password) < 6:
            return render_template(
                "forgot_password.html",
                error="Password kam se kam 6 characters ka rakho", **stage2_kwargs
            ), 400
        if new_password != confirm_password:
            return render_template(
                "forgot_password.html", error="Dono password match nahi karte", **stage2_kwargs
            ), 400

        db = get_db()
        db.execute(
            "UPDATE users SET password = ? WHERE id = ?",
            (generate_password_hash(new_password), user["id"]),
        )
        db.commit()
        return render_template(
            "login.html", error=None,
            notice="Password reset ho gaya. Naye password se login karo.",
        )

    return render_template("forgot_password.html", stage=1, error=None)


# ---------- EXPENSE APIs ----------
@app.route("/api/add_expense", methods=["POST"])
@login_required
def add_expense():
    cleaned, error = validate_expense(request.get_json(silent=True))
    if error:
        return jsonify({"error": error}), 400

    db = get_db()
    cur = db.execute(
        "INSERT INTO expenses (user_id, amount, category, date, note) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            session["user_id"],
            cleaned["amount"],
            cleaned["category"],
            cleaned["date"],
            cleaned["note"],
        ),
    )
    db.commit()
    return jsonify({"status": "success", "id": cur.lastrowid}), 201


@app.route("/api/get_expenses")
@login_required
def get_expenses():
    """Optional filters: ?month=YYYY-MM  &category=Food"""
    query = "SELECT id, amount, category, date, note FROM expenses WHERE user_id = ?"
    params = [session["user_id"]]

    month = request.args.get("month")
    if month:
        if not MONTH_RE.match(month):
            return jsonify({"error": "month YYYY-MM format mein do"}), 400
        query += " AND substr(date, 1, 7) = ?"
        params.append(month)

    category = request.args.get("category")
    if category:
        query += " AND category = ?"
        params.append(category)

    query += " ORDER BY date DESC, id DESC"
    rows = get_db().execute(query, params).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/update_expense/<int:exp_id>", methods=["PUT"])
@login_required
def update_expense(exp_id):
    cleaned, error = validate_expense(request.get_json(silent=True))
    if error:
        return jsonify({"error": error}), 400

    db = get_db()
    cur = db.execute(
        "UPDATE expenses SET amount = ?, category = ?, date = ?, note = ? "
        "WHERE id = ? AND user_id = ?",
        (
            cleaned["amount"],
            cleaned["category"],
            cleaned["date"],
            cleaned["note"],
            exp_id,
            session["user_id"],
        ),
    )
    db.commit()
    if cur.rowcount == 0:
        return jsonify({"error": "Expense nahi mila"}), 404
    return jsonify({"status": "updated"})


@app.route("/api/delete_expense/<int:exp_id>", methods=["DELETE"])
@login_required
def delete_expense(exp_id):
    db = get_db()
    cur = db.execute(
        "DELETE FROM expenses WHERE id = ? AND user_id = ?",
        (exp_id, session["user_id"]),
    )
    db.commit()
    if cur.rowcount == 0:
        return jsonify({"error": "Expense nahi mila"}), 404
    return jsonify({"status": "deleted"})


# ---------- BUDGET + SUMMARY APIs ----------
@app.route("/api/budget", methods=["POST"])
@login_required
def set_budget():
    """Body: {"category": "Food", "monthly_limit": 5000}"""
    data = request.get_json(silent=True) or {}
    category = str(data.get("category", "")).strip()
    limit = parse_amount(data.get("monthly_limit"))

    if category not in CATEGORIES:
        return jsonify({"error": "Invalid category"}), 400
    if limit is None:
        return jsonify({"error": "Limit 0 se bada valid number hona chahiye"}), 400

    db = get_db()
    db.execute(
        "INSERT INTO budgets (user_id, category, monthly_limit) VALUES (?, ?, ?) "
        "ON CONFLICT (user_id, category) DO UPDATE SET monthly_limit = excluded.monthly_limit",
        (session["user_id"], category, limit),
    )
    db.commit()
    return jsonify({"status": "saved"})


@app.route("/api/salary", methods=["POST"])
@login_required
def set_salary():
    """Body: {"amount": 50000, "month": "2026-09"}  (month na do toh is mahine ki)"""
    data = request.get_json(silent=True) or {}
    month = str(data.get("month") or datetime.now().strftime("%Y-%m"))
    if not MONTH_RE.match(month):
        return jsonify({"error": "month YYYY-MM format mein do"}), 400

    amount = parse_amount(data.get("amount"))
    if amount is None:
        return jsonify({"error": "Salary 0 se badi valid amount honi chahiye"}), 400

    db = get_db()
    db.execute(
        "INSERT INTO salaries (user_id, month, amount, skipped) VALUES (?, ?, ?, 0) "
        "ON CONFLICT (user_id, month) DO UPDATE SET amount = excluded.amount, skipped = 0",
        (session["user_id"], month, amount),
    )
    db.commit()
    return jsonify({"status": "saved"})


@app.route("/api/salary/skip", methods=["POST"])
@login_required
def skip_salary():
    """User ne is mahine ke liye salary prompt skip kiya. Saved salary ko chhedta nahi."""
    data = request.get_json(silent=True) or {}
    month = str(data.get("month") or datetime.now().strftime("%Y-%m"))
    if not MONTH_RE.match(month):
        return jsonify({"error": "month YYYY-MM format mein do"}), 400

    db = get_db()
    db.execute(
        "INSERT INTO salaries (user_id, month, amount, skipped) VALUES (?, ?, NULL, 1) "
        "ON CONFLICT (user_id, month) DO NOTHING",
        (session["user_id"], month),
    )
    db.commit()
    return jsonify({"status": "skipped"})


@app.route("/api/add_income", methods=["POST"])
@login_required
def add_income():
    """Body: {"amount": 2000, "source": "Freelance", "date": "2026-09-10"}"""
    data = request.get_json(silent=True) or {}
    amount = parse_amount(data.get("amount"))
    source = str(data.get("source", "")).strip()[:100]
    date = str(data.get("date", "")).strip()

    if amount is None:
        return jsonify({"error": "Amount 0 se bada valid number hona chahiye"}), 400
    if not source:
        return jsonify({"error": "Source likhna zaroori hai"}), 400
    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        return jsonify({"error": "Date YYYY-MM-DD format mein honi chahiye"}), 400

    db = get_db()
    cur = db.execute(
        "INSERT INTO incomes (user_id, amount, source, date) VALUES (?, ?, ?, ?)",
        (session["user_id"], amount, source, date),
    )
    db.commit()
    return jsonify({"status": "success", "id": cur.lastrowid}), 201


@app.route("/api/get_incomes")
@login_required
def get_incomes():
    """Optional: ?month=YYYY-MM"""
    query = "SELECT id, amount, source, date FROM incomes WHERE user_id = ?"
    params = [session["user_id"]]

    month = request.args.get("month")
    if month:
        if not MONTH_RE.match(month):
            return jsonify({"error": "month YYYY-MM format mein do"}), 400
        query += " AND substr(date, 1, 7) = ?"
        params.append(month)

    query += " ORDER BY date DESC, id DESC"
    rows = get_db().execute(query, params).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/delete_income/<int:income_id>", methods=["DELETE"])
@login_required
def delete_income(income_id):
    db = get_db()
    cur = db.execute(
        "DELETE FROM incomes WHERE id = ? AND user_id = ?",
        (income_id, session["user_id"]),
    )
    db.commit()
    if cur.rowcount == 0:
        return jsonify({"error": "Entry nahi mila"}), 404
    return jsonify({"status": "deleted"})


@app.route("/api/summary")
@login_required
def summary():
    """Dashboard/charts ka data. Optional: ?month=YYYY-MM (default: is mahine)"""
    month = request.args.get("month") or datetime.now().strftime("%Y-%m")
    if not MONTH_RE.match(month):
        return jsonify({"error": "month YYYY-MM format mein do"}), 400

    db = get_db()
    uid = session["user_id"]

    by_category = db.execute(
        "SELECT category, SUM(amount) AS total FROM expenses "
        "WHERE user_id = ? AND substr(date, 1, 7) = ? "
        "GROUP BY category ORDER BY total DESC",
        (uid, month),
    ).fetchall()

    trend = db.execute(
        "SELECT substr(date, 1, 7) AS month, SUM(amount) AS total FROM expenses "
        "WHERE user_id = ? GROUP BY month ORDER BY month DESC LIMIT 6",
        (uid,),
    ).fetchall()

    budgets = db.execute(
        "SELECT category, monthly_limit FROM budgets WHERE user_id = ?", (uid,)
    ).fetchall()

    spent = {r["category"]: r["total"] for r in by_category}
    total = round(sum(spent.values()), 2)

    salary_row = db.execute(
        "SELECT amount, skipped FROM salaries WHERE user_id = ? AND month = ?",
        (uid, month),
    ).fetchone()
    salary = salary_row["amount"] if salary_row else None
    salary_skipped = bool(salary_row and salary_row["skipped"])

    extra_income = db.execute(
        "SELECT COALESCE(SUM(amount), 0) AS total FROM incomes "
        "WHERE user_id = ? AND substr(date, 1, 7) = ?",
        (uid, month),
    ).fetchone()["total"]
    has_income = salary is not None or extra_income > 0
    total_income = round((salary or 0) + extra_income, 2) if has_income else None

    budget_status = []
    for b in budgets:
        used = spent.get(b["category"], 0)
        percent = round(used / b["monthly_limit"] * 100, 1)
        if percent >= 100:
            status = "over"
        elif percent >= 80:
            status = "warning"
        else:
            status = "ok"
        budget_status.append(
            {
                "category": b["category"],
                "limit": b["monthly_limit"],
                "spent": used,
                "percent": percent,
                "status": status,
            }
        )

    return jsonify(
        {
            "month": month,
            "total": total,
            "salary": salary,
            "salary_skipped": salary_skipped,
            "extra_income": round(extra_income, 2),
            "total_income": total_income,
            "balance": round(total_income - total, 2) if has_income else None,
            "by_category": [dict(r) for r in by_category],
            "trend": [dict(r) for r in reversed(trend)],  # purana -> naya
            "budgets": budget_status,
        }
    )


# ---------- ACCOUNT ----------
@app.route("/api/change_password", methods=["POST"])
@login_required
def change_password():
    data = request.get_json(silent=True) or {}
    current = data.get("current_password", "")
    new = data.get("new_password", "")

    user = get_db().execute(
        "SELECT password FROM users WHERE id = ?", (session["user_id"],)
    ).fetchone()
    # 401 yahan client-side ke "session expired -> login page" logic se takra jata,
    # isliye galat current password ke liye 400 use kiya hai.
    if not user or not check_password_hash(user["password"], current):
        return jsonify({"error": "Current password is incorrect"}), 400
    if len(new) < 6:
        return jsonify({"error": "New password must be at least 6 characters"}), 400
    if new == current:
        return jsonify({"error": "New password must be different from the current one"}), 400

    db = get_db()
    db.execute(
        "UPDATE users SET password = ? WHERE id = ?",
        (generate_password_hash(new), session["user_id"]),
    )
    db.commit()
    return jsonify({"status": "updated"})


@app.route("/api/profile", methods=["POST"])
@login_required
def update_profile():
    """Body: {"username": "..."} and/or {"security_question": "...", "security_answer": "..."}"""
    data = request.get_json(silent=True) or {}
    new_username = str(data.get("username", "")).strip()
    new_question = str(data.get("security_question", "")).strip()
    new_answer = str(data.get("security_answer", "")).strip()

    sets, params = [], []

    if new_username:
        if not 3 <= len(new_username) <= 30:
            return jsonify({"error": "Username 3 se 30 characters ka hona chahiye"}), 400
        sets.append("username = ?")
        params.append(new_username)

    if new_question or new_answer:
        if new_question not in SECURITY_QUESTIONS:
            return jsonify({"error": "Ek valid security question chuno"}), 400
        if len(new_answer) < 2:
            return jsonify({"error": "Answer kam se kam 2 characters ka rakho"}), 400
        sets.append("security_question = ?")
        params.append(new_question)
        sets.append("security_answer = ?")
        params.append(generate_password_hash(normalize_answer(new_answer)))

    if not sets:
        return jsonify({"error": "Kuch toh badlo"}), 400

    db = get_db()
    params.append(session["user_id"])
    try:
        db.execute(f"UPDATE users SET {', '.join(sets)} WHERE id = ?", params)
        db.commit()
    except sqlite3.IntegrityError:
        return jsonify({"error": "Ye username pehle se le liya gaya hai"}), 409

    if new_username:
        session["username"] = new_username
    return jsonify({"status": "updated", "username": session["username"]})


# ---------- EXPORT ----------
def fetch_export_rows(db, uid, month):
    query = "SELECT date, category, amount, note FROM expenses WHERE user_id = ?"
    params = [uid]
    if month:
        query += " AND substr(date, 1, 7) = ?"
        params.append(month)
    query += " ORDER BY date"
    return db.execute(query, params).fetchall()


def export_csv_response(rows, label):
    """Mool (original) CSV export — format/behaviour bilkul same rakha gaya hai."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["Date", "Category", "Amount", "Note"])
    total = 0.0
    for r in rows:
        writer.writerow([r["date"], r["category"], f"{r['amount']:.2f}", r["note"] or ""])
        total += r["amount"]
    writer.writerow([])
    writer.writerow(["", "Total", f"{total:.2f}", ""])

    return Response(
        buffer.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename=expenses_{label}.csv"},
    )


def export_xlsx_response(rows, label):
    wb = Workbook()
    ws = wb.active
    ws.title = "Expenses"
    ws.append(["Date", "Category", "Amount", "Note"])
    for cell in ws[1]:
        cell.font = Font(bold=True)

    total = 0.0
    for r in rows:
        ws.append([r["date"], r["category"], round(r["amount"], 2), r["note"] or ""])
        total += r["amount"]

    ws.append([])
    total_row = ws.max_row + 1
    ws.append(["", "Total", round(total, 2), ""])
    ws.cell(row=total_row, column=2).font = Font(bold=True)
    ws.cell(row=total_row, column=3).font = Font(bold=True)

    for i, width in enumerate([12, 16, 12, 42], start=1):
        ws.column_dimensions[get_column_letter(i)].width = width
    ws.freeze_panes = "A2"

    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return Response(
        buffer.getvalue(),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename=expenses_{label}.xlsx"},
    )


def export_pdf_response(rows, label, username):
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4, title=f"Expenses {label}",
        leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm, bottomMargin=16 * mm,
    )
    styles = getSampleStyleSheet()
    elements = [
        Paragraph(f"Expense report &mdash; {'All time' if label == 'all' else label}", styles["Title"]),
    ]
    if username:
        elements.append(Paragraph(f"Account: {username}", styles["Normal"]))
    elements.append(Spacer(1, 10))

    data = [["Date", "Category", "Amount (INR)", "Note"]]
    total = 0.0
    for r in rows:
        note = r["note"] or ""
        if len(note) > 60:
            note = note[:57] + "..."
        data.append([r["date"], r["category"], f"{r['amount']:.2f}", note])
        total += r["amount"]

    if len(data) == 1:
        elements.append(Paragraph("No expenses found for this period.", styles["Normal"]))
    else:
        data.append(["", "", "", ""])
        data.append(["", "Total", f"{total:.2f}", ""])
        table = Table(data, colWidths=[24 * mm, 30 * mm, 28 * mm, 90 * mm], repeatRows=1)
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F2A5A")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("ALIGN", (2, 1), (2, -1), "RIGHT"),
            ("GRID", (0, 0), (-1, -3), 0.4, colors.HexColor("#D8DCE8")),
            ("LINEABOVE", (0, -1), (-1, -1), 0.8, colors.HexColor("#1F2A5A")),
            ("FONTNAME", (1, -1), (2, -1), "Helvetica-Bold"),
            ("ROWBACKGROUNDS", (0, 1), (-1, -3), [colors.white, colors.HexColor("#F4F6FB")]),
        ]))
        elements.append(table)

    doc.build(elements)
    buffer.seek(0)
    return Response(
        buffer.getvalue(),
        mimetype="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=expenses_{label}.pdf"},
    )


@app.route("/api/export")
@login_required
def export_expenses():
    """Download. Optional: ?month=YYYY-MM (default: sab mahine) &format=csv|xlsx|pdf (default csv)"""
    month = request.args.get("month")
    if month and not MONTH_RE.match(month):
        return jsonify({"error": "month YYYY-MM format mein do"}), 400

    fmt = (request.args.get("format") or "csv").strip().lower()
    if fmt not in EXPORT_FORMATS:
        return jsonify({"error": "format csv, xlsx ya pdf mein se ek hona chahiye"}), 400

    rows = fetch_export_rows(get_db(), session["user_id"], month)
    label = month or "all"

    if fmt == "csv":
        return export_csv_response(rows, label)
    if fmt == "xlsx":
        return export_xlsx_response(rows, label)
    return export_pdf_response(rows, label, session.get("username"))


# ---------- IMPORT ----------
@app.route("/api/import_expenses", methods=["POST"])
@login_required
def import_expenses():
    """CSV upload (multipart form, field name 'file'). Header required: Date, Category, Amount
    (Note optional); same column names jo Export CSV deta hai. Har row validate_expense() se
    hi guzarti hai, isliye add-expense wali saari rules (valid category, YYYY-MM-DD date,
    amount > 0) yahan bhi apply hoti hain — core validation/behaviour change nahi hua."""
    file = request.files.get("file")
    if file is None or file.filename == "":
        return jsonify({"error": "CSV file chuno"}), 400

    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in IMPORT_ALLOWED_EXT:
        return jsonify({"error": "Sirf .csv file allowed hai"}), 400

    raw = file.read()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return jsonify({"error": "File UTF-8 encoding mein honi chahiye"}), 400

    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except StopIteration:
        return jsonify({"error": "File khaali hai"}), 400

    col_index = {}
    for i, col in enumerate(header):
        key = col.strip().lower()
        if key == "date":
            col_index["date"] = i
        elif key == "category":
            col_index["category"] = i
        elif key in ("amount", "amt"):
            col_index["amount"] = i
        elif key in ("note", "notes"):
            col_index["note"] = i

    missing = [c for c in ("date", "category", "amount") if c not in col_index]
    if missing:
        return jsonify({
            "error": f"CSV mein ye column(s) chahiye: {', '.join(missing)} "
                     f"(Export CSV jaisa header: Date, Category, Amount, Note)"
        }), 400

    def cell(row, name):
        idx = col_index.get(name)
        if idx is None or idx >= len(row):
            return ""
        return row[idx]

    to_insert = []
    errors = []
    skipped = 0
    row_limit_hit = False

    for line_no, row in enumerate(reader, start=2):
        if not row or all(not str(c).strip() for c in row):
            continue  # blank line

        category_val = cell(row, "category").strip()
        date_val = cell(row, "date").strip()
        amount_val = cell(row, "amount").strip()

        # Export CSV khud hi ek trailing "Total" row deta hai — usko chup-chaap skip karo
        if category_val.lower() == "total" and not date_val:
            continue

        if len(to_insert) >= IMPORT_MAX_ROWS:
            row_limit_hit = True
            break

        cleaned, error = validate_expense({
            "amount": amount_val,
            "category": category_val,
            "date": date_val,
            "note": cell(row, "note"),
        })
        if error:
            skipped += 1
            if len(errors) < IMPORT_MAX_ERRORS:
                errors.append(f"Row {line_no}: {error}")
            continue
        to_insert.append(cleaned)

    if row_limit_hit:
        errors.append(f"Row limit ({IMPORT_MAX_ROWS}) reached; remaining rows were not read")

    inserted = 0
    if to_insert:
        db = get_db()
        uid = session["user_id"]
        db.executemany(
            "INSERT INTO expenses (user_id, amount, category, date, note) VALUES (?, ?, ?, ?, ?)",
            [(uid, c["amount"], c["category"], c["date"], c["note"]) for c in to_insert],
        )
        db.commit()
        inserted = len(to_insert)

    return jsonify({
        "status": "done",
        "inserted": inserted,
        "skipped": skipped,
        "errors": errors,
    })


# ---------- SMART SUGGESTIONS (rule-based) ----------
# Ye LLM nahi hai: user ke apne data (pichhle mahine se comparison, budgets, salary)
# par rules chalte hain, isliye result predictable aur viva mein explain karna aasan hai.
FLEXIBLE_CATEGORIES = {"Food", "Travel", "Shopping", "Entertainment", "Other"}
FIXED_CATEGORIES = {"Rent", "Bills", "Education"}   # projection mein scale nahi hote (ek baar ka lump-sum)
MIN_CHANGE = 200          # ye rupaye se chhote badlav ignore
MAX_TIPS = 6
LEVEL_ORDER = {"alert": 0, "warn": 1, "good": 2, "info": 3}

CATEGORY_TIPS = {
    "Food": "Carry lunch or cook a few days a week and keep eating out for special days.",
    "Travel": "Use a monthly pass or shared rides for trips you make regularly.",
    "Rent": "Rent is fixed, so look at your flexible categories first.",
    "Shopping": "Wait 48 hours before buying anything you do not need right now.",
    "Bills": "Check for unused subscriptions and cheaper recharge or data plans.",
    "Entertainment": "Set a small weekly fun budget and look for student discounts.",
    "Health": "Health spending is usually necessary; check for student insurance or generics.",
    "Education": "Look for free or student-priced resources before paying for courses.",
    "Other": "Review what sits under Other and give repeating items their own category.",
}


def inr(amount):
    """1234567 -> ₹12,34,567 (Indian grouping)."""
    n = int(round(amount))
    sign = "-" if n < 0 else ""
    digits = str(abs(n))
    if len(digits) > 3:
        head, tail = digits[:-3], digits[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        digits = ",".join(parts + [tail])
    return f"{sign}₹{digits}"


def tip(level, title, text):
    return {"level": level, "title": title, "text": text}


def month_parts(month):
    year, mon = int(month[:4]), int(month[5:7])
    return year, mon, calendar.monthrange(year, mon)[1]


def prev_month_key(month):
    year, mon, _ = month_parts(month)
    return f"{year - 1}-12" if mon == 1 else f"{year}-{mon - 1:02d}"


def category_totals(db, uid, month, end_day):
    """Mahine ke shuru se end_day tak ka category-wise total."""
    rows = db.execute(
        "SELECT category, SUM(amount) AS total FROM expenses "
        "WHERE user_id = ? AND substr(date, 1, 7) = ? AND date <= ? GROUP BY category",
        (uid, month, f"{month}-{end_day:02d}"),
    ).fetchall()
    return {r["category"]: r["total"] for r in rows}


def build_insights(db, uid, month, today=None):
    today = today or datetime.now()
    current_key = today.strftime("%Y-%m")

    if month > current_key:
        return [tip("info", "This month has not started yet",
                    "Suggestions show up once you add expenses for this month.")]

    _, _, days_in_month = month_parts(month)
    is_current = month == current_key
    end_day = today.day if is_current else days_in_month
    remaining = days_in_month - end_day

    cur = category_totals(db, uid, month, end_day)
    cur_total = sum(cur.values())
    if cur_total == 0:
        return [tip("info", "No expenses yet",
                    "Add a few expenses and this section will suggest where to cut back or save.")]

    # Comparison: current mahine mein "abhi tak" vs pichhle mahine ke usi din tak
    prev_key = prev_month_key(month)
    _, _, prev_days = month_parts(prev_key)
    prev = category_totals(db, uid, prev_key, min(end_day, prev_days))
    prev_total = sum(prev.values())
    basis = "the same days last month" if is_current else "the previous month"

    budgets = {
        r["category"]: r["monthly_limit"]
        for r in db.execute(
            "SELECT category, monthly_limit FROM budgets WHERE user_id = ?", (uid,)
        ).fetchall()
    }
    salary_row = db.execute(
        "SELECT amount FROM salaries WHERE user_id = ? AND month = ? AND amount IS NOT NULL",
        (uid, month),
    ).fetchone()
    extra_income = db.execute(
        "SELECT COALESCE(SUM(amount), 0) AS total FROM incomes "
        "WHERE user_id = ? AND substr(date, 1, 7) = ?",
        (uid, month),
    ).fetchone()["total"]
    has_income = salary_row is not None or extra_income > 0
    income_total = (salary_row["amount"] if salary_row else 0) + extra_income

    tips = []
    used_categories = set()
    cutting_back = False   # user pehle se kharcha ghata raha hai: use "bada hissa" ki warning nahi

    # 1) Overall: pichhle mahine se zyada ya kam kharcha
    if prev_total > 0:
        change = (cur_total - prev_total) / prev_total * 100
        cutting_back = change <= -20
        if change >= 20:
            tips.append(tip(
                "warn", f"Spending is up {round(change)}% from {basis}",
                f"You spent {inr(cur_total)} against {inr(prev_total)}. "
                "The category suggestions below show what grew the most.",
            ))
        elif change <= -20:
            tips.append(tip(
                "good", f"Spending is down {round(abs(change))}% from {basis}",
                f"That is {inr(prev_total - cur_total)} less. "
                "Move that amount into savings now, before it gets spent.",
            ))
        else:
            tips.append(tip(
                "info", "Spending is steady",
                f"You are within 20% of {basis} ({inr(cur_total)} vs {inr(prev_total)}).",
            ))

        # 2) Category jo sabse badhi / sabse ghati
        changes = [(c, cur.get(c, 0), prev.get(c, 0)) for c in set(cur) | set(prev)]
        ups = [x for x in changes
               if x[1] - x[2] >= MIN_CHANGE and (x[2] == 0 or (x[1] - x[2]) / x[2] >= 0.3)]
        if ups:
            cat, c, p = max(ups, key=lambda x: x[1] - x[2])
            used_categories.add(cat)
            extra = "" if cat in budgets else " Set a monthly budget for it to get an early warning."
            if p:
                tips.append(tip(
                    "warn", f"{cat} spending is up {round((c - p) / p * 100)}%",
                    f"{cat} went from {inr(p)} to {inr(c)} compared with {basis}. "
                    f"{CATEGORY_TIPS.get(cat, '')}{extra}",
                ))
            else:
                tips.append(tip(
                    "warn", f"New spending on {cat}",
                    f"You spent {inr(c)} on {cat}, with nothing there in {basis}. "
                    f"{CATEGORY_TIPS.get(cat, '')}{extra}",
                ))
        downs = [x for x in changes
                 if x[2] > 0 and x[2] - x[1] >= MIN_CHANGE and (x[2] - x[1]) / x[2] >= 0.3]
        used_categories.update(x[0] for x in downs)
        if downs:
            cat, c, p = max(downs, key=lambda x: x[2] - x[1])
            tips.append(tip(
                "good", f"You cut {cat} spending by {inr(p - c)}",
                f"{cat} went from {inr(p)} to {inr(c)} compared with {basis}. Keep this up.",
            ))
    else:
        tips.append(tip(
            "info", "Not enough history yet",
            "Suggestions get sharper once you have expenses from the previous month to compare with.",
        ))

    # 3) Ek flexible category bahut badi hai
    top_cat, top_val = max(cur.items(), key=lambda kv: kv[1])
    share = top_val / cur_total * 100
    if (share >= 40 and top_cat in FLEXIBLE_CATEGORIES
            and top_cat not in used_categories and not cutting_back):
        tips.append(tip(
            "warn", f"{top_cat} is {round(share)}% of your spending",
            f"At {inr(top_val)} it is your biggest category, so cutting back here saves the most. "
            f"{CATEGORY_TIPS.get(top_cat, '')}",
        ))

    # 4) Salary + extra income ke hisaab se
    if has_income:
        income = income_total
        if is_current:
            if cur_total >= income:
                left_text = (f" with {remaining} days left" if remaining > 0 else "")
                tips.append(tip(
                    "alert", "Your income is fully spent",
                    f"You have spent {inr(cur_total)} of {inr(income)}{left_text}. "
                    "Hold off on anything that is not essential until your next income.",
                ))
            elif end_day >= 5:
                # Rent/bills jaise fixed kharche jaise hain waise gine jate hain; baaki kharcha
                # ab tak ke daily pace se mahine ke end tak badhaya jata hai.
                fixed = sum(v for c, v in cur.items() if c in FIXED_CATEGORIES)
                projected = fixed + (cur_total - fixed) / end_day * days_in_month
                if projected > income:
                    daily = (income - cur_total) / remaining if remaining > 0 else 0
                    tips.append(tip(
                        "alert", f"At this pace you will overspend by {inr(projected - income)}",
                        f"You are on track to spend about {inr(projected)} against income of {inr(income)}. "
                        f"Keep spending under {inr(daily)} a day for the remaining {remaining} days to stay inside it.",
                    ))
                elif projected <= income * 0.8:
                    tips.append(tip(
                        "good", f"On track to save about {inr(income - projected)}",
                        f"At the current pace you will spend about {inr(projected)} of your {inr(income)} income. "
                        "Move the savings out early so they do not get spent.",
                    ))
                else:
                    tips.append(tip(
                        "info", "Your pace is close to your income",
                        f"You are on track to spend about {inr(projected)} of {inr(income)}. Stay steady.",
                    ))
        else:
            saved = income - cur_total
            rate = saved / income * 100
            if saved < 0:
                tips.append(tip("alert", f"You overspent your income by {inr(-saved)}",
                                "Check which categories grew and set budgets for them next month."))
            elif rate >= 20:
                tips.append(tip("good", f"You saved {round(rate)}% of your income",
                                f"That is {inr(saved)}. A common target is 20%, and you reached it."))
            else:
                tips.append(tip("warn" if rate < 10 else "info", f"You saved {round(rate)}% of your income",
                                f"A common target is 20%, which would be {inr(income * 0.2)}. "
                                "Trimming your biggest flexible category is the easiest way to get there."))
    elif is_current:
        tips.append(tip(
            "info", "Add your salary for savings tips",
            "With your salary or an extra income entry added, you get a month-end projection and a daily spending limit.",
        ))

    # 5) Budgets
    over, near = [], []
    for cat, limit in budgets.items():
        used = cur.get(cat, 0)
        if used > limit:
            over.append(f"{cat} ({inr(used)} of {inr(limit)})")
        elif is_current and remaining > 0 and used / limit >= 0.8:
            near.append((cat, used, limit))
    if over:
        noun = "category is" if len(over) == 1 else "categories are"
        tips.append(tip(
            "alert", f"{len(over)} {noun} over budget",
            f"Over: {', '.join(over)}. Cut back on these, or raise the limit if it was set too low.",
        ))
    for cat, used, limit in near:
        tips.append(tip(
            "warn", f"{cat} budget is {round(used / limit * 100)}% used",
            f"{inr(limit - used)} is left for the next {remaining} days, about "
            f"{inr((limit - used) / remaining)} a day.",
        ))

    tips.sort(key=lambda t: LEVEL_ORDER[t["level"]])  # sort stable hai, order bacha rehta hai
    return tips[:MAX_TIPS]


@app.route("/api/yearly")
@login_required
def yearly_summary():
    """Poore saal ka mahine-wise total. Optional: ?year=YYYY (default: is saal)"""
    year = request.args.get("year") or datetime.now().strftime("%Y")
    if not YEAR_RE.match(year):
        return jsonify({"error": "year YYYY format mein do"}), 400

    uid = session["user_id"]
    rows = get_db().execute(
        "SELECT substr(date, 6, 2) AS mon, SUM(amount) AS total FROM expenses "
        "WHERE user_id = ? AND substr(date, 1, 4) = ? GROUP BY mon",
        (uid, year),
    ).fetchall()
    totals = {r["mon"]: r["total"] for r in rows}
    months = [
        {"month": f"{m:02d}", "total": round(totals.get(f"{m:02d}", 0), 2)}
        for m in range(1, 13)
    ]

    cat_rows = get_db().execute(
        "SELECT category, SUM(amount) AS total FROM expenses "
        "WHERE user_id = ? AND substr(date, 1, 4) = ? GROUP BY category ORDER BY total DESC",
        (uid, year),
    ).fetchall()

    return jsonify({
        "year": year,
        "months": months,
        "total": round(sum(m["total"] for m in months), 2),
        "by_category": [{"category": r["category"], "total": round(r["total"], 2)} for r in cat_rows],
    })


@app.route("/api/compare")
@login_required
def compare_months():
    """Do mahino ka side-by-side comparison. ?month_a=YYYY-MM&month_b=YYYY-MM"""
    month_a = request.args.get("month_a") or datetime.now().strftime("%Y-%m")
    month_b = request.args.get("month_b") or prev_month_key(month_a)
    for m in (month_a, month_b):
        if not MONTH_RE.match(m):
            return jsonify({"error": "month_a/month_b YYYY-MM format mein do"}), 400

    uid = session["user_id"]
    db = get_db()
    totals_a = category_totals(db, uid, month_a, month_parts(month_a)[2])
    totals_b = category_totals(db, uid, month_b, month_parts(month_b)[2])

    by_category = []
    for cat in CATEGORIES:
        a, b = totals_a.get(cat, 0), totals_b.get(cat, 0)
        if a == 0 and b == 0:
            continue
        by_category.append({"category": cat, "a": round(a, 2), "b": round(b, 2), "diff": round(a - b, 2)})
    by_category.sort(key=lambda r: r["a"] + r["b"], reverse=True)

    total_a, total_b = round(sum(totals_a.values()), 2), round(sum(totals_b.values()), 2)
    return jsonify({
        "month_a": month_a, "month_b": month_b,
        "total_a": total_a, "total_b": total_b,
        "diff": round(total_a - total_b, 2),
        "by_category": by_category,
    })


@app.route("/api/insights")
@login_required
def insights():
    """Smart suggestions. Optional: ?month=YYYY-MM (default: is mahine)"""
    month = request.args.get("month") or datetime.now().strftime("%Y-%m")
    if not MONTH_RE.match(month):
        return jsonify({"error": "month YYYY-MM format mein do"}), 400
    return jsonify({"month": month, "insights": build_insights(get_db(), session["user_id"], month)})


# ---------- ERROR HANDLERS ----------
@app.errorhandler(404)
def not_found(e):
    if request.path.startswith("/api/"):
        return jsonify({"error": "Not found"}), 404
    return "Page not found", 404


@app.errorhandler(413)
def too_large(e):
    return jsonify({"error": "File 5MB se badi hai"}), 413


# ---------- MAIN ----------
init_db()  # gunicorn/Render par bhi tables ban jayengi

if __name__ == "__main__":
    app.run(debug=os.environ.get("FLASK_DEBUG", "1") == "1")
