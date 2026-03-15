"""
routes/loans.py
Loan / EMI blueprint — routes only. All business logic in services/loan_service.py.
"""

from datetime import datetime, date as date_cls
from dateutil.relativedelta import relativedelta

from flask import (Blueprint, render_template, redirect, url_for,
                   request, flash, session, jsonify)

from routes.db import get_db, login_required, get_monthly_spend
from services.loan_service import (
    LOAN_TYPES, LOAN_TYPE_META, LOAN_EMI_CATEGORY,
    calculate_emi, build_amortization, enrich_loan,
    get_current_balance, loan_stats, generate_insights,
    apply_extra_payment,
)

loans_bp = Blueprint('loans_bp', __name__)


@loans_bp.route('/loans')
@login_required
def loans():
    db  = get_db()
    uid = session['user_id']
    status_filter = request.args.get('status', 'active')
    if status_filter == 'all':
        raw = db.execute(
            'SELECT * FROM loans WHERE user_id=? ORDER BY status ASC, created_at DESC', (uid,)
        ).fetchall()
    else:
        raw = db.execute(
            'SELECT * FROM loans WHERE user_id=? AND status=? ORDER BY created_at DESC',
            (uid, status_filter)
        ).fetchall()

    enriched = []
    for l in raw:
        d = dict(l)
        d['remaining_balance'] = get_current_balance(db, l['id'], l['loan_amount'])
        enriched.append(enrich_loan(db, d))

    stats    = loan_stats(db, uid)
    insights = generate_insights(db, uid, stats, enriched)
    m_exp, m_pct = get_monthly_spend()

    return render_template('loans/loans.html',
        loans=enriched, stats=stats, insights=insights,
        loan_types=LOAN_TYPES, loan_type_meta=LOAN_TYPE_META,
        status_filter=status_filter,
        sidebar_expense=m_exp, sidebar_pct=m_pct,
    )


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
    loan['remaining_balance'] = get_current_balance(db, loan_id, loan['loan_amount'])
    loan = enrich_loan(db, loan)

    schedule_raw = db.execute(
        'SELECT * FROM loan_schedule WHERE loan_id=? ORDER BY month_number ASC', (loan_id,)
    ).fetchall()

    today = date_cls.today().isoformat()
    schedule = []
    for row in schedule_raw:
        r = dict(row)
        if r['payment_date'] < today:
            r['state'] = 'paid'
        elif r['payment_date'][:7] == today[:7]:
            r['state'] = 'current'
        else:
            r['state'] = 'upcoming'
        schedule.append(r)

    paid_count = sum(1 for r in schedule if r['state'] == 'paid')
    m_exp, m_pct = get_monthly_spend()

    return render_template('loans/loan_detail.html',
        loan=loan, schedule=schedule, paid_count=paid_count,
        loan_types=LOAN_TYPES,
        sidebar_expense=m_exp, sidebar_pct=m_pct,
    )


@loans_bp.route('/loans/add', methods=['POST'])
@login_required
def add_loan():
    db  = get_db()
    uid = session['user_id']

    name       = request.form.get('loan_name', '').strip()
    ltype      = request.form.get('loan_type', '').strip()
    amount_str = request.form.get('loan_amount', '').strip()
    rate_str   = request.form.get('interest_rate', '').strip()
    tenure_str = request.form.get('tenure_months', '').strip()
    start      = request.form.get('start_date', '').strip()
    proc_str   = request.form.get('processing_fee', '0').strip() or '0'
    ins_str    = request.form.get('insurance', '0').strip() or '0'

    errors = []
    if not all([name, ltype, amount_str, rate_str, tenure_str, start]):
        errors.append('Please fill all required fields.')
    try:
        amount    = float(amount_str)
        rate      = float(rate_str)
        tenure    = int(tenure_str)
        proc_fee  = float(proc_str)
        insurance = float(ins_str)
        if amount <= 0:   errors.append('Loan amount must be positive.')
        if rate < 0:      errors.append('Interest rate cannot be negative.')
        if tenure <= 0:   errors.append('Tenure must be greater than zero.')
        if amount > 1e9:  errors.append('Loan amount seems unrealistic.')
    except ValueError:
        errors.append('Invalid number format.')
        amount = rate = proc_fee = insurance = 0; tenure = 1

    if errors:
        for e in errors:
            flash(e, 'error')
        return redirect(url_for('loans_bp.loans'))

    calc = calculate_emi(amount, rate, tenure)
    emi  = calc['monthly_emi']

    try:
        start_date = datetime.strptime(start, '%Y-%m-%d').date()
    except ValueError:
        start_date = date_cls.today()

    cur = db.execute(
        'INSERT INTO loans (user_id, loan_name, loan_type, loan_amount, interest_rate, '
        'tenure_months, start_date, monthly_emi, total_interest, total_payment, '
        'processing_fee, insurance, status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
        (uid, name, ltype, amount, rate, tenure, start,
         emi, calc['total_interest'], calc['total_payment'],
         proc_fee, insurance, 'active')
    )
    loan_id = cur.lastrowid

    for row in build_amortization(amount, rate, tenure, emi, start_date):
        db.execute(
            'INSERT INTO loan_schedule (loan_id, month_number, emi, interest_component, '
            'principal_component, remaining_balance, payment_date) VALUES (?,?,?,?,?,?,?)',
            (loan_id, row['month_number'], row['emi'], row['interest_component'],
             row['principal_component'], row['remaining_balance'], row['payment_date'])
        )

    due_day      = min(start_date.day, 28)
    end_date_str = (start_date + relativedelta(months=tenure)).isoformat()
    db.execute(
        'INSERT INTO obligations (user_id, name, amount, frequency, due_day, '
        'start_date, end_date, status, source_type, source_id, category) '
        'VALUES (?,?,?,?,?,?,?,?,?,?,?)',
        (uid, f'{name} EMI', emi, 'monthly', due_day, start,
         end_date_str, 'active', 'loan', loan_id, LOAN_EMI_CATEGORY)
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

    db.execute(
        'UPDATE loans SET loan_name=?, processing_fee=?, insurance=? WHERE id=? AND user_id=?',
        (name, proc, ins, loan_id, uid)
    )
    db.execute(
        "UPDATE obligations SET name=? WHERE source_type='loan' AND source_id=? AND user_id=?",
        (f'{name} EMI', loan_id, uid)
    )
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
    db.execute(
        "UPDATE obligations SET status='closed' WHERE source_type='loan' AND source_id=? AND user_id=?",
        (loan_id, uid)
    )
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
    db.execute(
        "DELETE FROM obligations WHERE source_type='loan' AND source_id=? AND user_id=?",
        (loan_id, uid)
    )
    db.execute('DELETE FROM loans WHERE id=? AND user_id=?', (loan_id, uid))
    db.commit()
    flash(f'Loan "{loan["loan_name"]}" deleted.', 'success')
    return redirect(url_for('loans_bp.loans'))


@loans_bp.route('/loans/extra-payment/<int:loan_id>', methods=['POST'])
@login_required
def extra_payment(loan_id):
    db  = get_db()
    uid = session['user_id']
    try:
        amount = float(request.form.get('extra_amount', 0))
        if amount <= 0:
            raise ValueError
    except ValueError:
        flash('Please enter a valid payment amount.', 'error')
        return redirect(url_for('loans_bp.loan_detail', loan_id=loan_id))

    result = apply_extra_payment(db, loan_id, uid, amount)
    if 'error' in result:
        flash(result['error'], 'error')
    elif result['fully_paid']:
        flash(
            f'Loan fully paid! You saved ₹{result["interest_saved"]:,.0f} in interest '
            f'and {result["months_saved"]} months. 🎉',
            'success'
        )
        return redirect(url_for('loans_bp.loans'))
    else:
        flash(
            f'Extra payment of ₹{amount:,.0f} applied. '
            f'Interest saved: ₹{result["interest_saved"]:,.0f} · '
            f'Months reduced: {result["months_saved"]}.',
            'success'
        )
    return redirect(url_for('loans_bp.loan_detail', loan_id=loan_id))


@loans_bp.route('/loans/api/calc-emi', methods=['POST'])
@login_required
def api_calc_emi():
    data = request.get_json(silent=True) or {}
    try:
        p = float(data.get('amount', 0))
        r = float(data.get('rate', 0))
        n = int(data.get('months', 0))
        if p <= 0 or n <= 0:
            raise ValueError
        return jsonify(calculate_emi(p, r, n))
    except (ValueError, TypeError, ZeroDivisionError):
        return jsonify({'error': 'invalid'}), 400


def get_loan_summary(db, uid: int) -> dict:
    """Called from analytics/health score modules."""
    return loan_stats(db, uid)