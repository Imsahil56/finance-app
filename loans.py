"""
routes/loans.py
Loan / EMI management blueprint.
Loans are separate from transactions — they represent fixed obligations.
"""

from datetime import datetime, date as date_cls
import math

from flask import (Blueprint, render_template, redirect, url_for,
                   request, flash, session)

from routes.db import get_db, login_required, get_monthly_spend

loans_bp = Blueprint('loans_bp', __name__)

LOAN_TYPES = [
    'Home Loan', 'Car Loan', 'Personal Loan',
    'Education Loan', 'Credit Card Loan', 'Other',
]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_loans(db, uid: int, status: str = None):
    if status:
        return db.execute(
            'SELECT * FROM loans WHERE user_id=? AND status=? ORDER BY created_at DESC',
            (uid, status)
        ).fetchall()
    return db.execute(
        'SELECT * FROM loans WHERE user_id=? ORDER BY status ASC, created_at DESC',
        (uid,)
    ).fetchall()


def _loan_stats(db, uid: int):
    """Return summary stats used on the loans page and for analytics."""
    active = db.execute(
        "SELECT * FROM loans WHERE user_id=? AND status='active'", (uid,)
    ).fetchall()

    total_active        = len(active)
    total_monthly_emi   = sum(r['monthly_emi'] for r in active)
    total_remaining     = sum(r['remaining_balance'] for r in active)

    # Next EMI date = earliest end_date among active loans that's still in future
    today = date_cls.today()
    future_ends = [
        r['end_date'] for r in active
        if r['end_date'] and r['end_date'] >= today.isoformat()
    ]
    next_emi_date = min(future_ends) if future_ends else None

    # Monthly income for debt-to-income
    monthly_income = db.execute(
        "SELECT COALESCE(SUM(amount),0) as s FROM txn "
        "WHERE user_id=? AND type='income' "
        "AND strftime('%m',date)=? AND strftime('%Y',date)=?",
        (uid, f'{today.month:02d}', str(today.year))
    ).fetchone()['s']

    debt_to_income = round(total_monthly_emi / monthly_income * 100, 1) if monthly_income else 0

    return {
        'total_active':      total_active,
        'total_monthly_emi': total_monthly_emi,
        'total_remaining':   total_remaining,
        'next_emi_date':     next_emi_date,
        'monthly_income':    monthly_income,
        'debt_to_income':    debt_to_income,
    }


def _enrich_loan(loan):
    """Add computed fields to a loan row dict."""
    d = dict(loan)
    amount  = d.get('loan_amount') or 0
    balance = d.get('remaining_balance') or amount
    paid    = max(0, amount - balance)
    d['paid_amount'] = paid
    d['paid_pct']    = min(round(paid / amount * 100) if amount else 0, 100)

    # Month calculations
    try:
        start = datetime.strptime(d['start_date'], '%Y-%m-%d').date()
        end   = datetime.strptime(d['end_date'],   '%Y-%m-%d').date()
        today = date_cls.today()
        total_months     = max(1, (end.year - start.year) * 12 + (end.month - start.month))
        completed_months = max(0, (today.year - start.year) * 12 + (today.month - start.month))
        completed_months = min(completed_months, total_months)
        remaining_months = max(0, total_months - completed_months)
        d['total_months']     = total_months
        d['completed_months'] = completed_months
        d['remaining_months'] = remaining_months
        d['timeline_pct']     = min(round(completed_months / total_months * 100) if total_months else 0, 100)
    except Exception:
        d['total_months'] = d['completed_months'] = d['remaining_months'] = d['timeline_pct'] = 0

    return d


# ── Routes ────────────────────────────────────────────────────────────────────

@loans_bp.route('/loans')
@login_required
def loans():
    db  = get_db()
    uid = session['user_id']

    status_filter = request.args.get('status', 'all')
    if status_filter in ('active', 'closed'):
        all_loans = _get_loans(db, uid, status=status_filter)
    else:
        all_loans = _get_loans(db, uid)

    enriched = [_enrich_loan(l) for l in all_loans]
    stats    = _loan_stats(db, uid)
    m_exp, m_pct = get_monthly_spend()

    return render_template('loans/loans.html',
        loans=enriched, stats=stats,
        loan_types=LOAN_TYPES,
        status_filter=status_filter,
        sidebar_expense=m_exp, sidebar_pct=m_pct,
    )


@loans_bp.route('/loan/<int:loan_id>')
@login_required
def loan_detail(loan_id):
    db  = get_db()
    uid = session['user_id']
    loan = db.execute(
        'SELECT * FROM loans WHERE id=? AND user_id=?', (loan_id, uid)
    ).fetchone()
    if not loan:
        flash('Loan not found.', 'error')
        return redirect(url_for('loans_bp.loans'))

    loan = _enrich_loan(loan)
    m_exp, m_pct = get_monthly_spend()
    return render_template('loans/loan_detail.html',
        loan=loan, loan_types=LOAN_TYPES,
        sidebar_expense=m_exp, sidebar_pct=m_pct,
    )


@loans_bp.route('/loans/add', methods=['POST'])
@login_required
def add_loan():
    db  = get_db()
    uid = session['user_id']

    name      = request.form.get('loan_name', '').strip()
    ltype     = request.form.get('loan_type', '').strip()
    amount    = request.form.get('loan_amount', '').strip()
    emi       = request.form.get('monthly_emi', '').strip()
    rate      = request.form.get('interest_rate', '').strip() or None
    start     = request.form.get('start_date', '').strip()
    end       = request.form.get('end_date', '').strip()
    balance   = request.form.get('remaining_balance', '').strip() or None

    if not all([name, ltype, amount, emi, start, end]):
        flash('Please fill all required fields.', 'error')
        return redirect(url_for('loans_bp.loans'))

    try:
        amount_f  = float(amount)
        emi_f     = float(emi)
        rate_f    = float(rate)   if rate    else None
        balance_f = float(balance) if balance else amount_f
    except ValueError:
        flash('Invalid number format.', 'error')
        return redirect(url_for('loans_bp.loans'))

    db.execute(
        'INSERT INTO loans (user_id, loan_name, loan_type, loan_amount, monthly_emi, '
        'interest_rate, start_date, end_date, remaining_balance, status) '
        'VALUES (?,?,?,?,?,?,?,?,?,?)',
        (uid, name, ltype, amount_f, emi_f, rate_f, start, end, balance_f, 'active')
    )
    db.commit()
    flash(f'Loan "{name}" added successfully.', 'success')
    return redirect(url_for('loans_bp.loans'))


@loans_bp.route('/loans/edit/<int:loan_id>', methods=['POST'])
@login_required
def edit_loan(loan_id):
    db  = get_db()
    uid = session['user_id']
    loan = db.execute(
        'SELECT * FROM loans WHERE id=? AND user_id=?', (loan_id, uid)
    ).fetchone()
    if not loan:
        flash('Loan not found.', 'error')
        return redirect(url_for('loans_bp.loans'))

    name    = request.form.get('loan_name', '').strip()
    amount  = request.form.get('loan_amount', '').strip()
    emi     = request.form.get('monthly_emi', '').strip()
    rate    = request.form.get('interest_rate', '').strip() or None
    end     = request.form.get('end_date', '').strip()
    balance = request.form.get('remaining_balance', '').strip() or None

    try:
        db.execute(
            'UPDATE loans SET loan_name=?, loan_amount=?, monthly_emi=?, interest_rate=?, '
            'end_date=?, remaining_balance=? WHERE id=? AND user_id=?',
            (name, float(amount), float(emi),
             float(rate) if rate else None,
             end,
             float(balance) if balance else float(amount),
             loan_id, uid)
        )
        db.commit()
        flash(f'Loan "{name}" updated.', 'success')
    except (ValueError, TypeError):
        flash('Invalid data.', 'error')

    ref = request.referrer or url_for('loans_bp.loans')
    return redirect(ref)


@loans_bp.route('/loans/close/<int:loan_id>', methods=['POST'])
@login_required
def close_loan(loan_id):
    db  = get_db()
    uid = session['user_id']
    loan = db.execute(
        'SELECT * FROM loans WHERE id=? AND user_id=?', (loan_id, uid)
    ).fetchone()
    if not loan:
        flash('Loan not found.', 'error')
        return redirect(url_for('loans_bp.loans'))

    db.execute(
        "UPDATE loans SET status='closed', remaining_balance=0 WHERE id=? AND user_id=?",
        (loan_id, uid)
    )
    db.commit()
    flash(f'Loan "{loan["loan_name"]}" marked as closed.', 'success')
    return redirect(url_for('loans_bp.loans'))


@loans_bp.route('/loans/delete/<int:loan_id>', methods=['POST'])
@login_required
def delete_loan(loan_id):
    db  = get_db()
    uid = session['user_id']
    loan = db.execute(
        'SELECT * FROM loans WHERE id=? AND user_id=?', (loan_id, uid)
    ).fetchone()
    if not loan:
        flash('Loan not found.', 'error')
        return redirect(url_for('loans_bp.loans'))

    db.execute('DELETE FROM loans WHERE id=? AND user_id=?', (loan_id, uid))
    db.commit()
    flash(f'Loan "{loan["loan_name"]}" deleted.', 'success')
    return redirect(url_for('loans_bp.loans'))


# ── Analytics helper (called from analytics/health score) ─────────────────────

def get_loan_summary(db, uid: int):
    """
    Returns loan data for analytics integration.
    Keys: total_monthly_emi, total_remaining, debt_to_income, active_count
    """
    return _loan_stats(db, uid)
