"""
routes/transactions.py
Transactions blueprint — list, add, edit, delete.
"""

from datetime import date

from flask import (Blueprint, render_template, redirect, url_for,
                   request, flash, session)

from routes.db import (get_db, login_required, get_monthly_spend,
                       EXPENSE_CATEGORIES, INCOME_CATEGORIES)

transactions_bp = Blueprint('transactions_bp', __name__)


@transactions_bp.route('/transactions')
@login_required
def transactions():
    db  = get_db()
    uid = session['user_id']
    now = date.today()

    all_t = db.execute(
        'SELECT * FROM txn WHERE user_id=? ORDER BY date DESC, created_at DESC',
        (uid,)
    ).fetchall()

    # Sidebar widget data
    monthly_rows = db.execute(
        'SELECT type, amount FROM txn WHERE user_id=? '
        'AND strftime("%m", date)=? AND strftime("%Y", date)=?',
        (uid, f'{now.month:02d}', str(now.year))
    ).fetchall()
    monthly_expense = sum(r['amount'] for r in monthly_rows if r['type'] == 'expense')

    monthly_budget = db.execute(
        'SELECT * FROM monthly_budget WHERE user_id=? AND month=? AND year=?',
        (uid, now.month, now.year)
    ).fetchone()
    monthly_budget_pct = 0
    if monthly_budget and monthly_budget['budget_amount']:
        monthly_budget_pct = min(
            round(monthly_expense / monthly_budget['budget_amount'] * 100), 100
        )

    return render_template(
        'transaction/transactions.html',
        transactions=all_t,
        monthly_expense=monthly_expense,
        monthly_budget_pct=monthly_budget_pct,
    )


@transactions_bp.route('/add-transaction', methods=['GET', 'POST'])
@login_required
def add_transaction():
    if request.method == 'POST':
        t_type      = request.form.get('type')
        amount      = request.form.get('amount')
        category    = request.form.get('category')
        description = request.form.get('description', '').strip()
        t_date      = request.form.get('date')

        if not all([t_type, amount, category, t_date]):
            flash('Please fill in all required fields.', 'error')
        else:
            try:
                db = get_db()
                uid = session['user_id']
                is_recurring = 1 if request.form.get('is_recurring') else 0
                cur = db.execute(
                    'INSERT INTO txn (user_id, type, amount, category, description, date, '
                    'source_type, is_recurring) VALUES (?,?,?,?,?,?,?,?)',
                    (uid, t_type, float(amount), category, description, t_date,
                     'manual', is_recurring)
                )
                # Only create a Commitment obligation for recurring expenses, not income
                if is_recurring and t_type == 'expense':
                    try:
                        from datetime import date as _date
                        today = _date.today()
                        db.execute(
                            'INSERT INTO obligations (user_id, name, amount, frequency, due_day, '
                            'start_date, status, source_type) VALUES (?,?,?,?,?,?,?,?)',
                            (uid, description or category, float(amount), 'monthly',
                             min(today.day, 28), t_date, 'active', 'manual')
                        )
                    except Exception:
                        pass  # Don't block txn if obligation fails
                db.commit()
                flash('Transaction added!', 'success')
                return redirect(url_for('transactions_bp.transactions'))
            except Exception as exc:
                flash('Error saving transaction.', 'error')

    m_exp, m_pct = get_monthly_spend()
    return render_template(
        'transaction/add_transaction.html',
        expense_categories=EXPENSE_CATEGORIES,
        income_categories=INCOME_CATEGORIES,
        today=date.today().isoformat(),
        monthly_expense=m_exp,
        monthly_budget_pct=m_pct,
    )


@transactions_bp.route('/edit-transaction/<int:id>', methods=['GET', 'POST'])
@login_required
def edit_transaction(id):
    db = get_db()
    t  = db.execute(
        'SELECT * FROM txn WHERE id=? AND user_id=?', (id, session['user_id'])
    ).fetchone()

    if not t:
        flash('Transaction not found.', 'error')
        return redirect(url_for('transactions_bp.transactions'))

    if request.method == 'POST':
        t_type      = request.form.get('type')
        amount      = request.form.get('amount')
        category    = request.form.get('category')
        description = request.form.get('description', '').strip()
        t_date      = request.form.get('date')

        if not all([t_type, amount, category, t_date]):
            flash('Please fill in all required fields.', 'error')
        else:
            try:
                db.execute(
                    'UPDATE txn SET type=?, amount=?, category=?, description=?, date=? '
                    'WHERE id=?',
                    (t_type, float(amount), category, description, t_date, id)
                )
                db.commit()
                flash('Transaction updated!', 'success')
                return redirect(url_for('transactions_bp.transactions'))
            except Exception:
                flash('Error updating transaction.', 'error')

    m_exp, m_pct = get_monthly_spend()
    return render_template(
        'transaction/edit_transaction.html',
        transaction=t,
        expense_categories=EXPENSE_CATEGORIES,
        income_categories=INCOME_CATEGORIES,
        monthly_expense=m_exp,
        monthly_budget_pct=m_pct,
    )


@transactions_bp.route('/delete-transaction/<int:id>', methods=['POST'])
@login_required
def delete_transaction(id):
    db = get_db()
    db.execute('DELETE FROM txn WHERE id=? AND user_id=?', (id, session['user_id']))
    db.commit()
    flash('Transaction deleted.', 'success')
    return redirect(url_for('transactions_bp.transactions'))