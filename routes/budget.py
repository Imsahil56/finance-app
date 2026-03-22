"""
routes/budget.py
Budget blueprint — monthly, yearly, and category budget management.
Now uses monthly_category_budget (per-month) instead of the old global category_budget.
"""

from datetime import datetime, date as date_cls

from flask import (Blueprint, render_template, redirect, url_for,
                   request, flash, session)

from routes.db import (get_db, login_required, get_monthly_spend,
                       EXPENSE_CATEGORIES)

budget_bp = Blueprint('budget_bp', __name__)


# ── Internal helpers ──────────────────────────────────────────────────────────

def _get_monthly_cat_budgets(db, uid, month, year):
    return db.execute(
        'SELECT * FROM monthly_category_budget '
        'WHERE user_id=? AND month=? AND year=? ORDER BY category',
        (uid, month, year)
    ).fetchall()


def _build_cat_budget_data(db, uid, month, year, cat_spend):
    rows = _get_monthly_cat_budgets(db, uid, month, year)
    is_monthly = bool(rows)

    # Fall back to defaults as preview if no monthly rows
    if not rows:
        rows = db.execute(
            'SELECT id, user_id, category, amount FROM default_category_budget '
            'WHERE user_id=? ORDER BY category', (uid,)
        ).fetchall()

    result = []
    for r in rows:
        amt     = r['amount']
        spent   = cat_spend.get(r['category'], 0)
        raw_pct = round(spent / amt * 100) if amt else 0
        status  = 'over' if raw_pct >= 100 else ('warn' if raw_pct >= 80 else 'ok')
        result.append({
            'id':         r['id'],
            'category':   r['category'],
            'budget':     amt,
            'spent':      spent,
            'pct':        min(raw_pct, 100),
            'raw_pct':    raw_pct,
            'status':     status,
            'is_monthly': is_monthly,
        })
    return result


# ── Routes ────────────────────────────────────────────────────────────────────

@budget_bp.route('/budget')
@login_required
def budget():
    db  = get_db()
    uid = session['user_id']
    now = datetime.now()
    month, year = now.month, now.year

    monthly_budget = db.execute(
        'SELECT * FROM monthly_budget WHERE user_id=? AND month=? AND year=?',
        (uid, month, year)
    ).fetchone()

    monthly_rows = db.execute(
        "SELECT type, category, amount FROM txn WHERE user_id=? "
        "AND strftime('%m',date)=? AND strftime('%Y',date)=?",
        (uid, f'{month:02d}', str(year))
    ).fetchall()
    monthly_expense = sum(r['amount'] for r in monthly_rows if r['type'] == 'expense')
    monthly_income  = sum(r['amount'] for r in monthly_rows if r['type'] == 'income')

    monthly_budget_pct = 0
    if monthly_budget and monthly_budget['budget_amount']:
        monthly_budget_pct = min(
            round(monthly_expense / monthly_budget['budget_amount'] * 100), 100
        )

    yearly_rows = db.execute(
        "SELECT type, amount FROM txn WHERE user_id=? AND strftime('%Y',date)=?",
        (uid, str(year))
    ).fetchall()
    yearly_expense = sum(r['amount'] for r in yearly_rows if r['type'] == 'expense')
    yearly_income  = sum(r['amount'] for r in yearly_rows if r['type'] == 'income')

    yearly_budget = db.execute(
        'SELECT * FROM monthly_budget WHERE user_id=? AND month=0 AND year=?',
        (uid, year)
    ).fetchone()
    yearly_budget_pct = 0
    if yearly_budget and yearly_budget['budget_amount']:
        yearly_budget_pct = min(
            round(yearly_expense / yearly_budget['budget_amount'] * 100), 100
        )

    cat_count = db.execute(
        'SELECT COUNT(*) FROM monthly_category_budget WHERE user_id=? AND month=? AND year=?',
        (uid, month, year)
    ).fetchone()[0]
    if cat_count == 0:
        cat_count = db.execute(
            'SELECT COUNT(*) FROM default_category_budget WHERE user_id=?', (uid,)
        ).fetchone()[0]

    total_allocated = db.execute(
        'SELECT COALESCE(SUM(amount),0) FROM monthly_category_budget '
        'WHERE user_id=? AND month=? AND year=?',
        (uid, month, year)
    ).fetchone()[0]

    import calendar as cal_mod
    from dateutil.relativedelta import relativedelta

    # ── Category spend this month ──────────────────────────────────────────────
    cat_spend = {}
    for r in monthly_rows:
        if r['type'] == 'expense':
            cat_spend[r['category']] = cat_spend.get(r['category'], 0) + r['amount']

    # ── Category budget data (monthly override or default) ─────────────────────
    cat_rows = db.execute(
        'SELECT category, amount FROM monthly_category_budget WHERE user_id=? AND month=? AND year=?',
        (uid, month, year)
    ).fetchall()
    if not cat_rows:
        cat_rows = db.execute(
            'SELECT category, amount FROM default_category_budget WHERE user_id=?', (uid,)
        ).fetchall()
    top_categories = []
    for c in cat_rows:
        spent   = cat_spend.get(c['category'], 0)
        raw_pct = round(spent / c['amount'] * 100) if c['amount'] else 0
        top_categories.append({
            'category': c['category'],
            'budget':   round(c['amount']),
            'spent':    round(spent),
            'pct':      min(raw_pct, 100),
            'raw_pct':  raw_pct,
            'status':   'over' if raw_pct >= 100 else ('warn' if raw_pct >= 80 else 'ok'),
        })
    top_categories.sort(key=lambda x: x['raw_pct'], reverse=True)

    # ── 6-month spending trend ─────────────────────────────────────────────────
    spending_trend = []
    for i in range(5, -1, -1):
        d = now + relativedelta(months=-i)
        s = db.execute(
            "SELECT COALESCE(SUM(amount),0) as s FROM txn "
            "WHERE user_id=? AND type='expense' "
            "AND strftime('%m',date)=? AND strftime('%Y',date)=?",
            (uid, f'{d.month:02d}', str(d.year))
        ).fetchone()['s']
        avg_s = db.execute(
            "SELECT COALESCE(AVG(t),0) as a FROM ("
            "  SELECT SUM(amount) as t FROM txn "
            "  WHERE user_id=? AND type='expense' "
            "  AND strftime('%Y-%m',date) < ? "
            "  GROUP BY strftime('%Y-%m',date) ORDER BY strftime('%Y-%m',date) DESC LIMIT 3"
            ")",
            (uid, d.strftime('%Y-%m'))
        ).fetchone()['a']
        spending_trend.append({
            'label':  d.strftime('%b').upper(),
            'amount': round(s),
            'avg':    round(avg_s),
        })

    # ── Critical insights ──────────────────────────────────────────────────────
    critical_insights = []
    over_cats = [c for c in top_categories if c['status'] == 'over']
    warn_cats = [c for c in top_categories if c['status'] == 'warn']
    ok_cats   = [c for c in top_categories if c['status'] == 'ok']

    for c in over_cats[:2]:
        critical_insights.append({
            'type':  'danger',
            'icon':  'warning',
            'title': f'Adjust {c["category"]} Budget',
            'body':  f'{c["raw_pct"]}% used. Consider reducing or adjusting your limit.',
            'color': '#f87171',
        })
    for c in warn_cats[:2]:
        critical_insights.append({
            'type':  'warn',
            'icon':  'bolt',
            'title': f'Immediate Action: {c["category"]}',
            'body':  f'{c["raw_pct"]}% used — only ₹{c["budget"] - c["spent"]:,} remaining.',
            'color': '#fbbf24',
        })
    if ok_cats:
        best = ok_cats[-1]
        critical_insights.append({
            'type':  'good',
            'icon':  'auto_awesome',
            'title': f'Optimize {best["category"]}',
            'body':  f'Only {best["raw_pct"]}% used — ahead of budget this month.',
            'color': '#818cf8',
        })
    critical_insights = critical_insights[:3]

    # ── Days remaining in month ────────────────────────────────────────────────
    days_total     = cal_mod.monthrange(year, month)[1]
    days_remaining = max(0, days_total - now.day)
    avg_daily      = round(monthly_expense / now.day, 0) if now.day > 0 else 0

    m_exp, m_pct = get_monthly_spend()
    return render_template('budget/budget_landing.html',
        monthly_budget=monthly_budget, monthly_budget_pct=monthly_budget_pct,
        monthly_expense=monthly_expense, monthly_income=monthly_income,
        yearly_budget=yearly_budget, yearly_budget_pct=yearly_budget_pct,
        yearly_expense=yearly_expense, yearly_income=yearly_income,
        cat_count=cat_count, total_allocated=total_allocated,
        month_name=now.strftime('%B'), year=year,
        top_categories=top_categories,
        spending_trend=spending_trend,
        critical_insights=critical_insights,
        days_remaining=days_remaining,
        avg_daily=avg_daily,
        sidebar_expense=m_exp, sidebar_pct=m_pct
    )


@budget_bp.route('/budget/monthly', methods=['GET', 'POST'])
@login_required
def budget_monthly():
    db  = get_db()
    uid = session['user_id']
    now = datetime.now()

    try:
        sel_month = int(request.args.get('month', now.month))
        sel_year  = int(request.args.get('year',  now.year))
    except ValueError:
        sel_month, sel_year = now.month, now.year

    if request.method == 'POST':
        action = request.form.get('action')
        pm = int(request.form.get('post_month', sel_month))
        py = int(request.form.get('post_year',  sel_year))

        if action == 'set_monthly':
            amt = request.form.get('budget_amount')
            if amt:
                db.execute(
                    'INSERT INTO monthly_budget (user_id, month, year, budget_amount) VALUES (?,?,?,?) '
                    'ON CONFLICT(user_id, month, year) DO UPDATE SET budget_amount=excluded.budget_amount',
                    (uid, pm, py, float(amt))
                )
                db.commit()
                flash('Monthly budget saved!', 'success')

        elif action == 'add_category':
            cat = request.form.get('category')
            amt = request.form.get('cat_budget_amount')
            if cat and amt:
                db.execute(
                    'INSERT INTO monthly_category_budget (user_id, category, month, year, amount) '
                    'VALUES (?,?,?,?,?) '
                    'ON CONFLICT(user_id, category, month, year) DO UPDATE SET amount=excluded.amount',
                    (uid, cat, pm, py, float(amt))
                )
                db.commit()
                flash(f'Budget for {cat} set for this month only!', 'success')

        elif action == 'apply_defaults':
            from routes.defaults import apply_defaults_to_month
            overwrite = request.form.get('overwrite', '0') == '1'
            count = apply_defaults_to_month(db, uid, pm, py, overwrite=overwrite)
            if count:
                msg = f'Reset {count} category budgets to defaults.' if overwrite else f'Added defaults for {count} categories.'
                flash(msg, 'success')
            else:
                flash('No default budgets set. Go to Default Budget Settings first.', 'info')

        return redirect(url_for('budget_bp.budget_monthly', month=pm, year=py))

    # ── GET ───────────────────────────────────────────────────────────────────
    monthly_budget = db.execute(
        'SELECT * FROM monthly_budget WHERE user_id=? AND month=? AND year=?',
        (uid, sel_month, sel_year)
    ).fetchone()

    monthly_rows = db.execute(
        "SELECT type, category, amount FROM txn WHERE user_id=? "
        "AND strftime('%m',date)=? AND strftime('%Y',date)=?",
        (uid, f'{sel_month:02d}', str(sel_year))
    ).fetchall()
    monthly_expense = sum(r['amount'] for r in monthly_rows if r['type'] == 'expense')
    monthly_income  = sum(r['amount'] for r in monthly_rows if r['type'] == 'income')

    cat_spend = {}
    for r in monthly_rows:
        if r['type'] == 'expense':
            cat_spend[r['category']] = cat_spend.get(r['category'], 0) + r['amount']

    cat_budget_data = _build_cat_budget_data(db, uid, sel_month, sel_year, cat_spend)

    monthly_budget_pct = 0
    if monthly_budget and monthly_budget['budget_amount']:
        monthly_budget_pct = min(
            round(monthly_expense / monthly_budget['budget_amount'] * 100), 100
        )

    budgeted_cats  = {r['category'] for r in _get_monthly_cat_budgets(db, uid, sel_month, sel_year)}
    available_cats = [c for c in EXPENSE_CATEGORIES if c not in budgeted_cats]

    has_defaults = db.execute(
        'SELECT 1 FROM default_category_budget WHERE user_id=? LIMIT 1', (uid,)
    ).fetchone() is not None

    month_options = []
    for i in range(11, -1, -1):
        m = (now.month - i - 1) % 12 + 1
        y = now.year + ((now.month - i - 1) // 12)
        month_options.append({'month': m, 'year': y,
                              'label': date_cls(y, m, 1).strftime('%B %Y')})

    # ── Extra data for new UI ─────────────────────────────────────────────────
    import calendar
    today_d = date_cls.today()
    days_total   = calendar.monthrange(sel_year, sel_month)[1]
    days_passed  = today_d.day if (sel_month == today_d.month and sel_year == today_d.year) else days_total
    days_remaining = max(0, days_total - days_passed)
    avg_daily_spend  = round(monthly_expense / days_passed, 2) if days_passed > 0 else 0
    projected_spend  = round(avg_daily_spend * days_total, 2)
    savings_goal     = round((monthly_budget['budget_amount'] - projected_spend), 2) if monthly_budget else 0

    # ── Insights for sidebar ──────────────────────────────────────────────────
    budget_insights = []
    for cb in cat_budget_data:
        if cb['status'] == 'over':
            budget_insights.append({
                'type': 'danger',
                'icon': 'warning',
                'title': f'{cb["category"]} Over-limit',
                'body': f'You\'ve exceeded your {cb["category"]} budget by ₹{cb["spent"] - cb["budget"]:,.0f}.',
                'color': '#ef4444',
            })
        elif cb['status'] == 'warn' and cb['raw_pct'] >= 90:
            budget_insights.append({
                'type': 'warn',
                'icon': 'trending_up',
                'title': f'{cb["category"]} Near Limit',
                'body': f'At {cb["raw_pct"]}% — only ₹{cb["budget"] - cb["spent"]:,.0f} remaining.',
                'color': '#f59e0b',
            })
        elif cb['status'] == 'ok' and cb['raw_pct'] < 50 and cb['spent'] > 0:
            budget_insights.append({
                'type': 'good',
                'icon': 'check_circle',
                'title': f'On Track: {cb["category"]}',
                'body': f'Spending is {100 - cb["raw_pct"]}% below your limit. Great discipline!',
                'color': '#22c55e',
            })
    budget_insights = budget_insights[:4]

    m_exp, m_pct = get_monthly_spend()
    return render_template('budget/budget_monthly.html',
        monthly_budget=monthly_budget, monthly_budget_pct=monthly_budget_pct,
        monthly_expense=monthly_expense, monthly_income=monthly_income,
        cat_budget_data=cat_budget_data,
        available_cats=available_cats,
        has_defaults=has_defaults,
        sel_month=sel_month, sel_year=sel_year,
        sel_month_name=datetime(sel_year, sel_month, 1).strftime('%B %Y'),
        month_options=month_options,
        expense_categories=EXPENSE_CATEGORIES,
        days_remaining=days_remaining,
        avg_daily_spend=avg_daily_spend,
        projected_spend=projected_spend,
        savings_goal=savings_goal,
        budget_insights=budget_insights,
        sidebar_expense=m_exp, sidebar_pct=m_pct
    )


@budget_bp.route('/budget/yearly', methods=['GET', 'POST'])
@login_required
def budget_yearly():
    db  = get_db()
    uid = session['user_id']
    now = datetime.now()

    try:
        sel_year = int(request.args.get('year', now.year))
    except ValueError:
        sel_year = now.year

    if request.method == 'POST':
        action = request.form.get('action', 'set_yearly')
        py = int(request.form.get('post_year', sel_year))
        if action == 'set_yearly':
            amt = request.form.get('budget_amount')
            if amt:
                db.execute(
                    'INSERT INTO monthly_budget (user_id, month, year, budget_amount) VALUES (?,?,?,?) '
                    'ON CONFLICT(user_id, month, year) DO UPDATE SET budget_amount=excluded.budget_amount',
                    (uid, 0, py, float(amt))
                )
                db.commit()
                flash('Yearly budget saved!', 'success')
        return redirect(url_for('budget_bp.budget_yearly', year=py))

    yearly_budget = db.execute(
        'SELECT * FROM monthly_budget WHERE user_id=? AND month=0 AND year=?',
        (uid, sel_year)
    ).fetchone()

    yearly_rows = db.execute(
        "SELECT type, category, amount FROM txn WHERE user_id=? AND strftime('%Y',date)=?",
        (uid, str(sel_year))
    ).fetchall()
    yearly_expense = sum(r['amount'] for r in yearly_rows if r['type'] == 'expense')
    yearly_income  = sum(r['amount'] for r in yearly_rows if r['type'] == 'income')

    cat_spend_yearly = {}
    for r in yearly_rows:
        if r['type'] == 'expense':
            cat_spend_yearly[r['category']] = cat_spend_yearly.get(r['category'], 0) + r['amount']

    # ── Per-category yearly budget logic ─────────────────────────────────────
    # For each category:
    #   - If monthly_category_budget rows exist for the year → sum them
    #   - For months with NO row set → use default_category_budget amount
    #   - Fall back to default × 12 if no monthly rows at all

    default_cats = {
        row['category']: row['amount']
        for row in db.execute(
            'SELECT category, amount FROM default_category_budget WHERE user_id=?', (uid,)
        ).fetchall()
    }

    # Get all monthly category budget rows for the year
    monthly_cat_rows = db.execute(
        'SELECT category, month, amount FROM monthly_category_budget '
        'WHERE user_id=? AND year=? ORDER BY category, month',
        (uid, sel_year)
    ).fetchall()

    # Group by category
    from collections import defaultdict
    cat_months = defaultdict(dict)
    for r in monthly_cat_rows:
        cat_months[r['category']][r['month']] = r['amount']

    # All categories = union of monthly rows + defaults
    all_cats = set(cat_months.keys()) | set(default_cats.keys())

    cat_budget_data = []
    for cat in sorted(all_cats):
        # For each of 12 months: use the monthly override if set, else default, else 0
        yearly_total = 0
        default_amt  = default_cats.get(cat, 0)
        months_overridden = 0
        for m in range(1, 13):
            if m in cat_months.get(cat, {}):
                yearly_total += cat_months[cat][m]
                if cat_months[cat][m] != default_amt:
                    months_overridden += 1
            else:
                yearly_total += default_amt

        spent   = cat_spend_yearly.get(cat, 0)
        raw_pct = round(spent / yearly_total * 100) if yearly_total else 0
        status  = 'over' if raw_pct >= 100 else ('warn' if raw_pct >= 80 else 'ok')
        source  = 'mixed' if months_overridden > 0 else ('auto' if cat_months.get(cat) else 'projection')
        cat_budget_data.append({
            'category':          cat,
            'budget':            round(yearly_total),
            'spent':             round(spent),
            'pct':               min(raw_pct, 100),
            'raw_pct':           raw_pct,
            'status':            status,
            'months_overridden': months_overridden,
            'source':            source,
        })

    # ── Monthly velocity (spend per month for the year) ───────────────────────
    monthly_velocity = []
    for m in range(1, 13):
        rows = db.execute(
            "SELECT COALESCE(SUM(amount),0) as s FROM txn "
            "WHERE user_id=? AND type='expense' "
            "AND strftime('%m',date)=? AND strftime('%Y',date)=?",
            (uid, f'{m:02d}', str(sel_year))
        ).fetchone()
        inc = db.execute(
            "SELECT COALESCE(SUM(amount),0) as s FROM txn "
            "WHERE user_id=? AND type='income' "
            "AND strftime('%m',date)=? AND strftime('%Y',date)=?",
            (uid, f'{m:02d}', str(sel_year))
        ).fetchone()
        # Monthly budget for this month
        mb = db.execute(
            'SELECT budget_amount FROM monthly_budget WHERE user_id=? AND month=? AND year=?',
            (uid, m, sel_year)
        ).fetchone()
        label = date_cls(sel_year, m, 1).strftime('%b').upper()
        monthly_velocity.append({
            'month':   m,
            'label':   label,
            'expense': round(rows['s']),
            'income':  round(inc['s']),
            'budget':  round(mb['budget_amount']) if mb else 0,
        })

    # ── Smart insights ────────────────────────────────────────────────────────
    yearly_insights = []
    # Peak spend month
    if monthly_velocity:
        peak = max(monthly_velocity, key=lambda x: x['expense'])
        if peak['expense'] > 0:
            yearly_insights.append({
                'section': 'PEAK EXPENDITURE',
                'title':   peak['label'],
                'body':    f'₹{peak["expense"]:,.0f} peak total spend',
                'color':   '#f87171',
                'icon':    'trending_up',
            })
    # Biggest over-budget category
    over_cats = [c for c in cat_budget_data if c['status'] == 'over']
    if over_cats:
        worst = max(over_cats, key=lambda x: x['raw_pct'])
        yearly_insights.append({
            'section': 'CRITICAL',
            'title':   worst['category'],
            'body':    f'Over by ₹{worst["spent"] - worst["budget"]:,.0f}',
            'color':   '#f87171',
            'icon':    'warning',
        })
    # Savings trend
    months_with_data = [m for m in monthly_velocity if m['expense'] > 0 or m['income'] > 0]
    if months_with_data:
        avg_save_rate = sum(
            (m['income'] - m['expense']) / m['income'] * 100
            for m in months_with_data if m['income'] > 0
        ) / max(len(months_with_data), 1)
        color = '#22c55e' if avg_save_rate > 0 else '#f87171'
        yearly_insights.append({
            'section': 'SAVINGS TREND',
            'title':   f'{avg_save_rate:+.0f}% avg savings rate',
            'body':    'Across months with data this year',
            'color':   color,
            'icon':    'savings',
        })

    yearly_budget_pct = 0
    if yearly_budget and yearly_budget['budget_amount']:
        yearly_budget_pct = min(
            round(yearly_expense / yearly_budget['budget_amount'] * 100), 100
        )

    year_options = list(range(now.year, now.year - 4, -1))
    m_exp, m_pct = get_monthly_spend()
    return render_template('budget/budget_yearly.html',
        yearly_budget=yearly_budget, yearly_budget_pct=yearly_budget_pct,
        yearly_expense=yearly_expense, yearly_income=yearly_income,
        cat_budget_data=cat_budget_data,
        monthly_velocity=monthly_velocity,
        yearly_insights=yearly_insights,
        sel_year=sel_year, year_options=year_options,
        sidebar_expense=m_exp, sidebar_pct=m_pct
    )


@budget_bp.route('/edit-category-budget/<int:mcb_id>', methods=['GET', 'POST'])
@login_required
def edit_category_budget(mcb_id):
    db  = get_db()
    uid = session['user_id']
    cb  = db.execute(
        'SELECT * FROM monthly_category_budget WHERE id=? AND user_id=?',
        (mcb_id, uid)
    ).fetchone()

    if not cb:
        flash('Budget not found.', 'error')
        return redirect(url_for('budget_bp.budget_monthly'))

    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'delete':
            db.execute('DELETE FROM monthly_category_budget WHERE id=? AND user_id=?', (mcb_id, uid))
            db.commit()
            flash(f'Budget for {cb["category"]} removed for this month.', 'success')
            return redirect(url_for('budget_bp.budget_monthly', month=cb['month'], year=cb['year']))

        amt = request.form.get('budget_amount')
        if amt:
            db.execute(
                'UPDATE monthly_category_budget SET amount=? WHERE id=? AND user_id=?',
                (float(amt), mcb_id, uid)
            )
            db.commit()
            flash(f'Budget for {cb["category"]} updated (this month only — default unchanged).', 'success')
            return redirect(url_for('budget_bp.budget_monthly', month=cb['month'], year=cb['year']))

    spent_row = db.execute(
        "SELECT COALESCE(SUM(amount),0) as s FROM txn "
        "WHERE user_id=? AND category=? AND type='expense' "
        "AND strftime('%m',date)=? AND strftime('%Y',date)=?",
        (uid, cb['category'], f'{cb["month"]:02d}', str(cb['year']))
    ).fetchone()
    spent = spent_row['s']
    pct   = min(round(spent / cb['amount'] * 100), 100) if cb['amount'] else 0

    default_row = db.execute(
        'SELECT * FROM default_category_budget WHERE user_id=? AND category=?',
        (uid, cb['category'])
    ).fetchone()

    m_exp, m_pct = get_monthly_spend()
    return render_template('budget/edit_category_budget.html',
        cb=cb, spent=spent, pct=pct,
        default_row=default_row,
        expense_categories=EXPENSE_CATEGORIES,
        month_name=date_cls(cb['year'], cb['month'], 1).strftime('%B %Y'),
        sidebar_expense=m_exp, sidebar_pct=m_pct
    )