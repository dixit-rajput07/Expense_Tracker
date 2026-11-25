# Expense Tracker

A Flask + SQLite personal expense tracker with budgets, salary/income tracking,
monthly & yearly summaries, smart spending insights, and CSV/Excel/PDF import-export.

## Features
- Add, edit, delete expenses by category with notes
- Monthly budgets per category with usage alerts
- Salary / extra income tracking with month-end savings projection
- Yearly summary and month-vs-month comparison
- Rule-based smart insights (spending pace, overspend warnings, savings tips)
- Export expenses as **CSV**, **Excel (.xlsx)**, or **PDF**
- Import expenses in bulk from a **CSV** file

## Setup

```bash
git clone https://github.com/dixit-rajput07/Expense_Tracker.git
cd <repo-folder>

python -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate

pip install -r requirements.txt
```

## Run locally

```bash
python app.py
```

Visit `http://127.0.0.1:5000`. The SQLite database (`database.db`) and its
tables are created automatically on first run.

## Environment variables (for deployment)

| Variable      | Purpose                                  |
|---------------|-------------------------------------------|
| `SECRET_KEY`  | Flask session secret — set a long random string |
| `FLASK_DEBUG` | Set to `0` in production                 |

## CSV import format

Header row: `Date, Category, Amount, Note` (same as the Export CSV output).
- `Date`: `YYYY-MM-DD`
- `Category`: one of the app's fixed categories (Food, Travel, Rent, Shopping,
  Bills, Entertainment, Health, Education, Other)
- `Amount`: a positive number
- `Note`: optional, max 200 characters

Invalid rows are skipped and reported; valid rows are still imported.
