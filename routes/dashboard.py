"""
routes/dashboard.py
Dashboard blueprint — summary cards with period filter (This Week / This Month / Overall).
"""

from collections import OrderedDict
from datetime import datetime, date, timedelta

from flask import Blueprint, render_template, session

from routes.db import get_db, login_required

dashboard_bp = Blueprint('dashboard_bp', __name__)


def _sum_rows(rows, t):
    return sum(r['amount'] for r in rows if r['type'] == t)


def _category_map(rows):
    cats = {}
    for r in rows:
        if r['type'] == 'expense':
            cats[r['category']] = cats.get(r['category'], 0) + r['amount']
    return cats


@dashboard_bp.route('/dashboard')
@login_required
def dashboard():
    db  = get_db()
    uid = session['user_id']
    now = datetime.now()
    today = date.today()
    month, year = now.month, now.year
    month_str = f'{month:02d}'

    # ── This-week date range (Mon–today) ─────────────────────────────────────
    week_start = today - timedelta(days=today.weekday())   # Monday
    week_start_str = week_start.isoformat()
    today_str      = today.isoformat()

    # ── Fetch rows for each period ────────────────────────────────────────────
    all_rows = db.execute(
        'SELECT type, category, amount, date FROM txn WHERE user_id=?', (uid,)
    ).fetchall()

    monthly_rows = [r for r in all_rows
                    if r['date'][:7] == f'{year}-{month_str}']

    weekly_rows  = [r for r in all_rows
                    if week_start_str <= r['date'] <= today_str]

    # ── Period summaries ──────────────────────────────────────────────────────
    def period_summary(rows):
        income  = _sum_rows(rows, 'income')
        expense = _sum_rows(rows, 'expense')
        return {
            'income':   income,
            'expense':  expense,
            'balance':  income - expense,
            'cats':     _category_map(rows),
        }

    p_overall = period_summary(all_rows)
    p_month   = period_summary(monthly_rows)
    p_week    = period_summary(weekly_rows)

    # ── Recent transactions ───────────────────────────────────────────────────
    recent = db.execute(
        'SELECT * FROM txn WHERE user_id=? ORDER BY date DESC, created_at DESC LIMIT 5',
        (uid,)
    ).fetchall()

    # ── Monthly budget ────────────────────────────────────────────────────────
    monthly_budget = db.execute(
        'SELECT * FROM monthly_budget WHERE user_id=? AND month=? AND year=?',
        (uid, month, year)
    ).fetchone()
    monthly_budget_amt = monthly_budget['budget_amount'] if monthly_budget else 0
    monthly_budget_pct = 0
    if monthly_budget_amt:
        monthly_budget_pct = min(
            round(p_month['expense'] / monthly_budget_amt * 100), 100
        )

    # ── Budget exhaustion date ─────────────────────────────────────────────
    budget_exhaust_label = None
    import calendar as _cal
    days_in_month  = _cal.monthrange(year, month)[1]
    days_elapsed   = max(today.day, 1)
    days_remaining_month = days_in_month - today.day
    if monthly_budget_amt and p_month['expense'] > 0:
        avg_daily = p_month['expense'] / days_elapsed
        remaining_budget = monthly_budget_amt - p_month['expense']
        if remaining_budget <= 0:
            budget_exhaust_label = 'Budget exhausted'
        elif avg_daily > 0:
            days_until_exhaust = remaining_budget / avg_daily
            if days_until_exhaust <= days_remaining_month:
                exhaust_date = today + timedelta(days=int(days_until_exhaust))
                budget_exhaust_label = exhaust_date.strftime('~%b %d')
            else:
                budget_exhaust_label = 'Ends month safely'

    # Weekly budget (pro-rated: monthly / 4.33)
    weekly_budget_amt = round(monthly_budget_amt / 4.33) if monthly_budget_amt else 0
    weekly_budget_pct = 0
    if weekly_budget_amt:
        weekly_budget_pct = min(round(p_week['expense'] / weekly_budget_amt * 100), 100)

    # ── Category budgets ──────────────────────────────────────────────────────
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
        spent = p_month['cats'].get(cb['category'], 0)
        pct   = min(round(spent / budget_amt * 100), 100) if budget_amt else 0
        cat_budget_data.append({
            'category': cb['category'],
            'budget':   budget_amt,
            'spent':    spent,
            'percent':  pct,
            'over':     spent > budget_amt,
        })

    # ── Income vs Expense chart ───────────────────────────────────────────────
    monthly_data: OrderedDict = OrderedDict()
    for row in sorted(all_rows, key=lambda r: r['date']):
        key = row['date'][:7]
        if key not in monthly_data:
            dt = datetime.strptime(key, '%Y-%m')
            monthly_data[key] = {
                'label':   dt.strftime('%b %y'),
                'income':  0.0,
                'expense': 0.0,
            }
        if row['type'] == 'income':
            monthly_data[key]['income']  += row['amount']
        else:
            monthly_data[key]['expense'] += row['amount']

    if len(monthly_data) == 1:
        key = next(iter(monthly_data))
        monthly_data[key]['label'] = datetime.strptime(key, '%Y-%m').strftime('%b')

    income_expense_chart = list(monthly_data.values())

    # ── Per-month category breakdown for donut chart dropdown ────────────────
    month_cats = {}   # { 'YYYY-MM': {'label': 'Jan 2026', 'cats': {...}} }
    for row in all_rows:
        key = row['date'][:7]
        if key not in month_cats:
            dt = datetime.strptime(key, '%Y-%m')
            month_cats[key] = {
                'label': dt.strftime('%B %Y'),
                'cats':  {}
            }
        if row['type'] == 'expense':
            c = row['category']
            month_cats[key]['cats'][c] = month_cats[key]['cats'].get(c, 0) + row['amount']

    # Sort months newest first for the dropdown
    sorted_month_cats = dict(sorted(month_cats.items(), reverse=True))

    # Overall cats (reuse p_overall)
    overall_cats = p_overall['cats']

    return render_template(
        'dashboard.html',
        # keep legacy vars for template compat
        total_income   = p_overall['income'],
        total_expense  = p_overall['expense'],
        balance        = p_overall['balance'],
        monthly_income = p_month['income'],
        monthly_expense= p_month['expense'],
        category_data  = p_month['cats'],
        # period data for JS switcher
        p_overall      = p_overall,
        p_month        = p_month,
        p_week         = p_week,
        monthly_budget_amt  = monthly_budget_amt,
        weekly_budget_amt   = weekly_budget_amt,
        monthly_budget_pct  = monthly_budget_pct,
        weekly_budget_pct   = weekly_budget_pct,
        budget_exhaust_label = budget_exhaust_label,
        days_remaining_month = days_remaining_month,
        # donut chart
        sorted_month_cats   = sorted_month_cats,
        overall_cats        = overall_cats,
        current_month_key   = f'{year}-{month_str}',
        # rest
        recent         = recent,
        monthly_budget = monthly_budget,
        cat_budget_data= cat_budget_data,
        month_name     = now.strftime('%B %Y'),
        week_range     = f"{week_start.strftime('%d %b')} – {today.strftime('%d %b')}",
        income_expense_chart = income_expense_chart,
    )