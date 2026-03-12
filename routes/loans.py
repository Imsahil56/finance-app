"""
routes/loans.py
Loan management — auto EMI calculation, amortization schedule, obligation creation.
"""

import math
from datetime import datetime, date as date_cls
from dateutil.relativedelta import relativedelta

from flask import (Blueprint, render_template, redirect, url_for,
                   request, flash, session, jsonify)

from routes.db import get_db, login_required, get_monthly_spend

loans_bp = Blueprint('loans_bp', __name__)

LOAN_TYPES = [
    'Home Loan', 'Car Loan', 'Personal Loan',
    'Education Loan', 'Credit Card Loan', 'Other',
]


def calculate_emi(principal: float, annual_rate: float, tenure_months: int) -> dict:
    if annual_rate == 0:
        emi = principal / tenure_months
        total_payment = principal
        total_interest = 0.0
    else:
        r = annual_rate / 12 / 100
        emi = principal * r * math.pow(1 + r, tenure_months) / (math.pow(1 + r, tenure_months) - 1)
        total_payment = emi * tenure_months
        total_interest = total_payment - principal
    return {
        'monthly_emi':    round(emi, 2),
        'total_interest': round(total_interest, 2),
        'total_payment':  round(total_payment, 2),
    }


def build_amortization(principal, annual_rate, tenure_months, monthly_emi, start_date):
    schedule = []
    balance = principal
    r = annual_rate / 12 / 100
    for month in range(1, tenure_months + 1):
        pay_date = start_date + relativedelta(months=month)
        if annual_rate == 0:
            interest_component  = 0.0
            principal_component = monthly_emi
        else:
            interest_component  = round(balance * r, 2)
            principal_component = round(monthly_emi - interest_component, 2)
        if month == tenure_months:
            principal_component = round(balance, 2)
        balance = max(0.0, round(balance - principal_component, 2))
        schedule.append({
            'month_number':        month,
            'emi':                 monthly_emi,
            'interest_component':  interest_component,
            'principal_component': principal_component,
            'remaining_balance':   balance,
            'payment_date':        pay_date.isoformat(),
        })
    return schedule


def _get_current_balance(db, loan_id, loan_amount):
    today = date_cls.today().isoformat()
    row = db.execute(
        'SELECT remaining_balance FROM loan_schedule '
        'WHERE loan_id=? AND payment_date<=? ORDER BY month_number DESC LIMIT 1',
        (loan_id, today)
    ).fetchone()
    return row['remaining_balance'] if row else loan_amount


def _enrich_loan(db, d):
    amount    = d.get('loan_amount') or 0
    remaining = d.get('remaining_balance', amount)
    paid      = max(0.0, amount - remaining)
    d['paid_amount'] = paid
    d['paid_pct']    = min(round(paid / amount * 100) if amount else 0, 100)
    try:
        start  = datetime.strptime(d['start_date'], '%Y-%m-%d').date()
        end    = start + relativedelta(months=d['tenure_months'])
        today  = date_cls.today()
        total  = d['tenure_months']
        done   = max(0, (today.year - start.year)*12 + (today.month - start.month))
        done   = min(done, total)
        d['end_date']         = end.isoformat()
        d['completed_months'] = done
        d['remaining_months'] = max(0, total - done)
        d['timeline_pct']     = min(round(done / total * 100) if total else 0, 100)
    except Exception:
        d['end_date'] = '—'
        d['completed_months'] = d['remaining_months'] = d['timeline_pct'] = 0
    return d


def _loan_stats(db, uid):
    active = db.execute(
        "SELECT * FROM loans WHERE user_id=? AND status='active'", (uid,)
    ).fetchall()
    total_active      = len(active)
    total_monthly_emi = sum(r['monthly_emi'] for r in active)
    total_remaining   = sum(_get_current_balance(db, r['id'], r['loan_amount']) for r in active)
    today = date_cls.today()
    monthly_income = db.execute(
        "SELECT COALESCE(SUM(amount),0) as s FROM txn "
        "WHERE user_id=? AND type='income' AND strftime('%m',date)=? AND strftime('%Y',date)=?",
        (uid, f'{today.month:02d}', str(today.year))
    ).fetchone()['s']
    debt_to_income = round(total_monthly_emi / monthly_income * 100, 1) if monthly_income else 0
    cutoff = (today + relativedelta(months=3)).isoformat()
    ending = 0
    for r in active:
        try:
            end = (datetime.strptime(r['start_date'], '%Y-%m-%d').date()
                   + relativedelta(months=r['tenure_months']))
            if end.isoformat() <= cutoff:
                ending += 1
        except Exception:
            pass
    return {
        'total_active': total_active, 'total_monthly_emi': total_monthly_emi,
        'total_remaining': total_remaining, 'debt_to_income': debt_to_income,
        'monthly_income': monthly_income, 'ending_soon': ending,
    }


@loans_bp.route('/loans')
@login_required
def loans():
    db  = get_db()
    uid = session['user_id']
    status_filter = request.args.get('status', 'active')
    if status_filter == 'all':
        all_loans = db.execute(
            'SELECT * FROM loans WHERE user_id=? ORDER BY status ASC, created_at DESC', (uid,)
        ).fetchall()
    else:
        all_loans = db.execute(
            'SELECT * FROM loans WHERE user_id=? AND status=? ORDER BY created_at DESC',
            (uid, status_filter)
        ).fetchall()
    enriched = []
    for l in all_loans:
        d = dict(l)
        d['remaining_balance'] = _get_current_balance(db, l['id'], l['loan_amount'])
        enriched.append(_enrich_loan(db, d))
    stats = _loan_stats(db, uid)
    m_exp, m_pct = get_monthly_spend()
    return render_template('loans/loans.html',
        loans=enriched, stats=stats, loan_types=LOAN_TYPES,
        status_filter=status_filter, sidebar_expense=m_exp, sidebar_pct=m_pct)


@loans_bp.route('/loan/<int:loan_id>')
@login_required
def loan_detail(loan_id):
    db  = get_db()
    uid = session['user_id']
    loan = db.execute('SELECT * FROM loans WHERE id=? AND user_id=?', (loan_id, uid)).fetchone()
    if not loan:
        flash('Loan not found.', 'error')
        return redirect(url_for('loans_bp.loans'))
    loan = dict(loan)
    loan['remaining_balance'] = _get_current_balance(db, loan_id, loan['loan_amount'])
    loan = _enrich_loan(db, loan)
    schedule = db.execute(
        'SELECT * FROM loan_schedule WHERE loan_id=? ORDER BY month_number ASC', (loan_id,)
    ).fetchall()
    today = date_cls.today().isoformat()
    enriched_schedule = []
    for row in schedule:
        r = dict(row)
        r['state'] = 'paid' if r['payment_date'] < today else ('current' if r['payment_date'] == today else 'upcoming')
        enriched_schedule.append(r)
    m_exp, m_pct = get_monthly_spend()
    return render_template('loans/loan_detail.html',
        loan=loan, schedule=enriched_schedule, loan_types=LOAN_TYPES,
        sidebar_expense=m_exp, sidebar_pct=m_pct)


@loans_bp.route('/loans/add', methods=['POST'])
@login_required
def add_loan():
    db  = get_db()
    uid = session['user_id']
    name         = request.form.get('loan_name', '').strip()
    ltype        = request.form.get('loan_type', '').strip()
    amount_str   = request.form.get('loan_amount', '').strip()
    rate_str     = request.form.get('interest_rate', '').strip()
    tenure_str   = request.form.get('tenure_months', '').strip()
    start        = request.form.get('start_date', '').strip()
    proc_str     = request.form.get('processing_fee', '0').strip() or '0'
    ins_str      = request.form.get('insurance', '0').strip() or '0'
    errors = []
    if not all([name, ltype, amount_str, rate_str, tenure_str, start]):
        errors.append('Please fill all required fields.')
    try:
        amount   = float(amount_str)
        rate     = float(rate_str)
        tenure   = int(tenure_str)
        proc_fee = float(proc_str)
        insurance = float(ins_str)
        if amount <= 0:  errors.append('Loan amount must be positive.')
        if rate < 0:     errors.append('Interest rate cannot be negative.')
        if tenure <= 0:  errors.append('Tenure must be greater than zero.')
        if amount > 1e9: errors.append('Loan amount seems unrealistic (> 1 Crore).')
    except ValueError:
        errors.append('Invalid number format.')
        amount = rate = proc_fee = insurance = 0; tenure = 1
    if errors:
        for e in errors: flash(e, 'error')
        return redirect(url_for('loans_bp.loans'))
    calc = calculate_emi(amount, rate, tenure)
    emi = calc['monthly_emi']
    try:
        start_date = datetime.strptime(start, '%Y-%m-%d').date()
    except ValueError:
        start_date = date_cls.today()
    cur = db.execute(
        'INSERT INTO loans (user_id, loan_name, loan_type, loan_amount, interest_rate, '
        'tenure_months, start_date, monthly_emi, total_interest, total_payment, '
        'processing_fee, insurance, status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
        (uid, name, ltype, amount, rate, tenure, start,
         emi, calc['total_interest'], calc['total_payment'], proc_fee, insurance, 'active')
    )
    loan_id = cur.lastrowid
    for row in build_amortization(amount, rate, tenure, emi, start_date):
        db.execute(
            'INSERT INTO loan_schedule (loan_id, month_number, emi, interest_component, '
            'principal_component, remaining_balance, payment_date) VALUES (?,?,?,?,?,?,?)',
            (loan_id, row['month_number'], row['emi'], row['interest_component'],
             row['principal_component'], row['remaining_balance'], row['payment_date'])
        )
    due_day     = min(start_date.day, 28)
    end_date_str = (start_date + relativedelta(months=tenure)).isoformat()
    db.execute(
        'INSERT INTO obligations (user_id, name, amount, frequency, due_day, '
        'start_date, end_date, status, source_type, source_id) VALUES (?,?,?,?,?,?,?,?,?,?)',
        (uid, f'{name} EMI', emi, 'monthly', due_day, start, end_date_str, 'active', 'loan', loan_id)
    )
    db.commit()
    flash(f'Loan "{name}" added. Monthly EMI = ₹{emi:,.0f} for {tenure} months.', 'success')
    return redirect(url_for('loans_bp.loan_detail', loan_id=loan_id))


@loans_bp.route('/loans/edit/<int:loan_id>', methods=['POST'])
@login_required
def edit_loan(loan_id):
    db  = get_db()
    uid = session['user_id']
    loan = db.execute('SELECT * FROM loans WHERE id=? AND user_id=?', (loan_id, uid)).fetchone()
    if not loan:
        flash('Loan not found.', 'error')
        return redirect(url_for('loans_bp.loans'))
    name = request.form.get('loan_name', loan['loan_name']).strip()
    proc = float(request.form.get('processing_fee', loan['processing_fee']) or 0)
    ins  = float(request.form.get('insurance', loan['insurance']) or 0)
    db.execute('UPDATE loans SET loan_name=?, processing_fee=?, insurance=? WHERE id=? AND user_id=?',
               (name, proc, ins, loan_id, uid))
    db.execute("UPDATE obligations SET name=? WHERE source_type='loan' AND source_id=? AND user_id=?",
               (f'{name} EMI', loan_id, uid))
    db.commit()
    flash(f'Loan "{name}" updated.', 'success')
    return redirect(url_for('loans_bp.loan_detail', loan_id=loan_id))


@loans_bp.route('/loans/close/<int:loan_id>', methods=['POST'])
@login_required
def close_loan(loan_id):
    db  = get_db()
    uid = session['user_id']
    loan = db.execute('SELECT * FROM loans WHERE id=? AND user_id=?', (loan_id, uid)).fetchone()
    if not loan:
        flash('Loan not found.', 'error')
        return redirect(url_for('loans_bp.loans'))
    db.execute("UPDATE loans SET status='closed' WHERE id=? AND user_id=?", (loan_id, uid))
    db.execute("UPDATE obligations SET status='closed' WHERE source_type='loan' AND source_id=? AND user_id=?",
               (loan_id, uid))
    db.commit()
    flash(f'Loan "{loan["loan_name"]}" closed.', 'success')
    return redirect(url_for('loans_bp.loans'))


@loans_bp.route('/loans/delete/<int:loan_id>', methods=['POST'])
@login_required
def delete_loan(loan_id):
    db  = get_db()
    uid = session['user_id']
    loan = db.execute('SELECT * FROM loans WHERE id=? AND user_id=?', (loan_id, uid)).fetchone()
    if not loan:
        flash('Loan not found.', 'error')
        return redirect(url_for('loans_bp.loans'))
    db.execute('DELETE FROM loan_schedule WHERE loan_id=?', (loan_id,))
    db.execute("DELETE FROM obligations WHERE source_type='loan' AND source_id=? AND user_id=?", (loan_id, uid))
    db.execute('DELETE FROM loans WHERE id=? AND user_id=?', (loan_id, uid))
    db.commit()
    flash(f'Loan "{loan["loan_name"]}" deleted.', 'success')
    return redirect(url_for('loans_bp.loans'))


@loans_bp.route('/loans/api/calc-emi', methods=['POST'])
@login_required
def api_calc_emi():
    data = request.get_json(silent=True) or {}
    try:
        p = float(data.get('amount', 0))
        r = float(data.get('rate', 0))
        n = int(data.get('months', 0))
        if p <= 0 or n <= 0: raise ValueError
        return jsonify(calculate_emi(p, r, n))
    except (ValueError, TypeError, ZeroDivisionError):
        return jsonify({'error': 'invalid'}), 400


def get_loan_summary(db, uid):
    return _loan_stats(db, uid)