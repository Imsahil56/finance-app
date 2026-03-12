"""
routes/dashboard.py
Dashboard blueprint — main overview page with charts and summary cards.
"""

from collections import OrderedDict
from datetime import datetime

from flask import Blueprint, render_template, session

from routes.db import get_db, login_required

dashboard_bp = Blueprint('dashboard_bp', __name__)


@dashboard_bp.route('/dashboard')
@login_required
def dashboard():
    db  = get_db()
    uid = session['user_id']
    now = datetime.now()
    month, year = now.month, now.year
    month_str   = f'{month:02d}'

    # ── All-time totals (balance cards) ──────────────────────────────────────
    rows = db.execute(
        'SELECT type, amount FROM txn WHERE user_id=?', (uid,)
    ).fetchall()
    total_income  = sum(r['amount'] for r in rows if r['type'] == 'income')
    total_expense = sum(r['amount'] for r in rows if r['type'] == 'expense')
    balance       = total_income - total_expense

    # ── Current-month totals ──────────────────────────────────────────────────
    monthly_rows = db.execute(
        "SELECT type, category, amount FROM txn "
        "WHERE user_id=? AND strftime('%m', date)=? AND strftime('%Y', date)=?",
        (uid, month_str, str(year))
    ).fetchall()
    monthly_income  = sum(r['amount'] for r in monthly_rows if r['type'] == 'income')
    monthly_expense = sum(r['amount'] for r in monthly_rows if r['type'] == 'expense')

    # ── Category spend (current month) ───────────────────────────────────────
    category_data: dict[str, float] = {}
    for r in monthly_rows:
        if r['type'] == 'expense':
            category_data[r['category']] = category_data.get(r['category'], 0) + r['amount']

    # ── Recent transactions ───────────────────────────────────────────────────
    recent = db.execute(
        'SELECT * FROM txn WHERE user_id=? ORDER BY date DESC, created_at DESC LIMIT 5',
        (uid,)
    ).fetchall()

    # ── Monthly budget progress ───────────────────────────────────────────────
    monthly_budget = db.execute(
        'SELECT * FROM monthly_budget WHERE user_id=? AND month=? AND year=?',
        (uid, month, year)
    ).fetchone()
    monthly_budget_pct = 0
    if monthly_budget and monthly_budget['budget_amount']:
        monthly_budget_pct = min(
            round(monthly_expense / monthly_budget['budget_amount'] * 100), 100
        )

    # ── Category budgets (monthly_category_budget, fall back to defaults) ────
    cat_budgets = db.execute(
        'SELECT * FROM monthly_category_budget WHERE user_id=? AND month=? AND year=?',
        (uid, month, year)
    ).fetchall()
    if not cat_budgets:
        cat_budgets = db.execute(
            'SELECT id, user_id, category, amount as budget_amount '
            'FROM default_category_budget WHERE user_id=?', (uid,)
        ).fetchall()
    cat_budget_data = []
    for cb in cat_budgets:
        budget_amt = cb['amount'] if 'amount' in cb.keys() else cb['budget_amount']
        spent = category_data.get(cb['category'], 0)
        pct   = min(round(spent / budget_amt * 100), 100) if budget_amt else 0
        cat_budget_data.append({
            'category': cb['category'],
            'budget':   budget_amt,
            'spent':    spent,
            'percent':  pct,
            'over':     spent > budget_amt,
        })

    # ── Income vs Expense chart (monthly, unique labels across years) ─────────
    all_txn_rows = db.execute(
        'SELECT date, type, amount FROM txn WHERE user_id=? ORDER BY date ASC',
        (uid,)
    ).fetchall()

    monthly_data: OrderedDict = OrderedDict()
    for row in all_txn_rows:
        key = row['date'][:7]           # YYYY-MM
        if key not in monthly_data:
            dt = datetime.strptime(key, '%Y-%m')
            monthly_data[key] = {
                'label':   dt.strftime('%b %y'),   # "Jan 25" — unique across years
                'income':  0.0,
                'expense': 0.0,
            }
        if row['type'] == 'income':
            monthly_data[key]['income']  += row['amount']
        else:
            monthly_data[key]['expense'] += row['amount']

    # Single-month edge case: drop the year suffix
    if len(monthly_data) == 1:
        key = next(iter(monthly_data))
        monthly_data[key]['label'] = datetime.strptime(key, '%Y-%m').strftime('%b')

    income_expense_chart = list(monthly_data.values())

    return render_template(
        'dashboard.html',
        total_income=total_income,
        total_expense=total_expense,
        balance=balance,
        monthly_income=monthly_income,
        monthly_expense=monthly_expense,
        category_data=category_data,
        recent=recent,
        monthly_budget=monthly_budget,
        monthly_budget_pct=monthly_budget_pct,
        cat_budget_data=cat_budget_data,
        month_name=now.strftime('%B %Y'),
        income_expense_chart=income_expense_chart,
    )
