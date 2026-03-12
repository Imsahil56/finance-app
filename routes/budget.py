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
        "SELECT type, amount FROM txn WHERE user_id=? "
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

    m_exp, m_pct = get_monthly_spend()
    return render_template('budget/budget_landing.html',
        monthly_budget=monthly_budget, monthly_budget_pct=monthly_budget_pct,
        monthly_expense=monthly_expense, monthly_income=monthly_income,
        yearly_budget=yearly_budget, yearly_budget_pct=yearly_budget_pct,
        yearly_expense=yearly_expense, yearly_income=yearly_income,
        cat_count=cat_count, total_allocated=total_allocated,
        month_name=now.strftime('%B'), year=year,
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

    # Yearly category budgets = sum of monthly rows for the year
    monthly_agg = db.execute(
        'SELECT category, SUM(amount) as yearly_total, COUNT(*) as months_set '
        'FROM monthly_category_budget WHERE user_id=? AND year=? '
        'GROUP BY category ORDER BY category',
        (uid, sel_year)
    ).fetchall()

    cat_budget_data = []
    for row in monthly_agg:
        cat    = row['category']
        budget = row['yearly_total']
        spent  = cat_spend_yearly.get(cat, 0)
        raw_pct = round(spent / budget * 100) if budget else 0
        status  = 'over' if raw_pct >= 100 else ('warn' if raw_pct >= 80 else 'ok')
        cat_budget_data.append({
            'category': cat, 'budget': budget, 'spent': spent,
            'pct': min(raw_pct, 100), 'raw_pct': raw_pct, 'status': status,
            'months_set': row['months_set'], 'source': 'auto',
        })

    # Fall back to defaults × 12 if no monthly category budgets for this year
    if not cat_budget_data:
        for d in db.execute(
            'SELECT * FROM default_category_budget WHERE user_id=? ORDER BY category', (uid,)
        ).fetchall():
            budget  = d['amount'] * 12
            spent   = cat_spend_yearly.get(d['category'], 0)
            raw_pct = round(spent / budget * 100) if budget else 0
            status  = 'over' if raw_pct >= 100 else ('warn' if raw_pct >= 80 else 'ok')
            cat_budget_data.append({
                'category': d['category'], 'budget': budget, 'spent': spent,
                'pct': min(raw_pct, 100), 'raw_pct': raw_pct, 'status': status,
                'months_set': 0, 'source': 'projection',
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