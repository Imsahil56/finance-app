"""
routes/defaults.py
Default Category Budget Template — CRUD for the template used to
auto-generate monthly category budgets.
"""

from flask import (Blueprint, render_template, redirect, url_for,
                   request, flash, session)

from routes.db import get_db, login_required, EXPENSE_CATEGORIES

defaults_bp = Blueprint('defaults_bp', __name__)


# ── Helpers ───────────────────────────────────────────────────────────────────

def get_defaults(db, uid: int):
    """Return all default category budgets for user, keyed by category."""
    rows = db.execute(
        'SELECT * FROM default_category_budget WHERE user_id=? ORDER BY category',
        (uid,)
    ).fetchall()
    return rows


def apply_defaults_to_month(db, uid: int, month: int, year: int, overwrite: bool = False):
    """
    Copy default_category_budget → monthly_category_budget for a given month.
    If overwrite=True: replaces ALL category budgets with defaults.
    If overwrite=False: only fills categories not yet set (preserves edits).
    """
    defaults = get_defaults(db, uid)
    if overwrite:
        # Delete existing monthly category budgets for this month first
        db.execute(
            'DELETE FROM monthly_category_budget WHERE user_id=? AND month=? AND year=?',
            (uid, month, year)
        )
    for d in defaults:
        db.execute(
            'INSERT OR IGNORE INTO monthly_category_budget '
            '(user_id, category, month, year, amount) VALUES (?,?,?,?,?)',
            (uid, d['category'], month, year, d['amount'])
        )
    db.commit()
    return len(defaults)


# ── Routes ────────────────────────────────────────────────────────────────────

@defaults_bp.route('/default-budgets')
@login_required
def default_budgets():
    db   = get_db()
    uid  = session['user_id']
    rows = get_defaults(db, uid)

    # Categories not yet in defaults (for the add form)
    existing_cats = {r['category'] for r in rows}
    available     = [c for c in EXPENSE_CATEGORIES if c not in existing_cats]

    total = sum(r['amount'] for r in rows)

    return render_template('budget/default_budgets.html',
        defaults=rows,
        available_categories=available,
        expense_categories=EXPENSE_CATEGORIES,
        total_default=total,
    )


@defaults_bp.route('/default-budgets/add', methods=['POST'])
@login_required
def add_default_budget():
    db  = get_db()
    uid = session['user_id']
    cat = request.form.get('category', '').strip()
    amt = request.form.get('amount', '').strip()

    if not cat or not amt:
        flash('Category and amount are required.', 'error')
        return redirect(url_for('defaults_bp.default_budgets'))

    if cat not in EXPENSE_CATEGORIES:
        flash('Invalid category.', 'error')
        return redirect(url_for('defaults_bp.default_budgets'))

    try:
        amount = float(amt)
        if amount <= 0:
            raise ValueError
    except ValueError:
        flash('Amount must be a positive number.', 'error')
        return redirect(url_for('defaults_bp.default_budgets'))

    db.execute(
        'INSERT INTO default_category_budget (user_id, category, amount) VALUES (?,?,?) '
        'ON CONFLICT(user_id, category) DO UPDATE SET amount=excluded.amount',
        (uid, cat, amount)
    )
    db.commit()
    flash(f'Default budget for {cat} set to ₹{amount:,.0f}.', 'success')
    return redirect(url_for('defaults_bp.default_budgets'))


@defaults_bp.route('/default-budgets/edit/<int:dcb_id>', methods=['GET', 'POST'])
@login_required
def edit_default_budget(dcb_id):
    db  = get_db()
    uid = session['user_id']
    row = db.execute(
        'SELECT * FROM default_category_budget WHERE id=? AND user_id=?',
        (dcb_id, uid)
    ).fetchone()

    if not row:
        flash('Default budget not found.', 'error')
        return redirect(url_for('defaults_bp.default_budgets'))

    if request.method == 'POST':
        amt = request.form.get('amount', '').strip()
        try:
            amount = float(amt)
            if amount <= 0:
                raise ValueError
        except ValueError:
            flash('Amount must be a positive number.', 'error')
            return redirect(url_for('defaults_bp.edit_default_budget', dcb_id=dcb_id))

        db.execute(
            'UPDATE default_category_budget SET amount=? WHERE id=? AND user_id=?',
            (amount, dcb_id, uid)
        )
        db.commit()
        flash(f'Default for {row["category"]} updated. Existing monthly budgets are NOT affected.', 'success')
        return redirect(url_for('defaults_bp.default_budgets'))

    return redirect(url_for('defaults_bp.default_budgets'))


@defaults_bp.route('/default-budgets/delete/<int:dcb_id>', methods=['POST'])
@login_required
def delete_default_budget(dcb_id):
    db  = get_db()
    uid = session['user_id']
    row = db.execute(
        'SELECT * FROM default_category_budget WHERE id=? AND user_id=?',
        (dcb_id, uid)
    ).fetchone()

    if not row:
        flash('Default budget not found.', 'error')
        return redirect(url_for('defaults_bp.default_budgets'))

    db.execute('DELETE FROM default_category_budget WHERE id=? AND user_id=?', (dcb_id, uid))
    db.commit()
    flash(f'Default for {row["category"]} deleted. Existing monthly budgets are NOT affected.', 'success')
    return redirect(url_for('defaults_bp.default_budgets'))


@defaults_bp.route('/default-budgets/apply', methods=['POST'])
@login_required
def apply_defaults():
    """Apply defaults to a specific month (fills missing rows only)."""
    db    = get_db()
    uid   = session['user_id']
    month = int(request.form.get('month'))
    year  = int(request.form.get('year'))

    count = apply_defaults_to_month(db, uid, month, year)
    if count:
        flash(f'Applied {count} default budgets to month. Existing entries were preserved.', 'success')
    else:
        flash('No default budgets defined yet.', 'info')

    return redirect(url_for('budget_bp.budget_monthly', month=month, year=year))