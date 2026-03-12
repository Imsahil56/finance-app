"""
routes/obligations.py
Obligation management — recurring commitments, pending payments, approve/skip workflow.
Notification API for the bell icon.
"""

from datetime import date as date_cls, timedelta
from dateutil.relativedelta import relativedelta
from datetime import datetime

from flask import (Blueprint, render_template, redirect, url_for,
                   request, flash, session, jsonify)

from routes.db import get_db, login_required, get_monthly_spend, EXPENSE_CATEGORIES

obligations_bp = Blueprint('obligations_bp', __name__)

FREQUENCIES = ['monthly', 'weekly', 'yearly']


# ── Helpers ───────────────────────────────────────────────────────────────────

def generate_pending_for_month(db, uid: int, month: int, year: int):
    """
    Create pending_obligation rows for all active obligations due this month.
    Safe to call multiple times (INSERT OR IGNORE).
    """
    obligations = db.execute(
        "SELECT * FROM obligations WHERE user_id=? AND status='active' AND frequency='monthly'",
        (uid,)
    ).fetchall()

    for ob in obligations:
        try:
            due_day = ob['due_day'] or 1
            due_date = date_cls(year, month, min(due_day, 28)).isoformat()
            # Check obligation is within its active range
            if ob['end_date'] and ob['end_date'] < due_date:
                continue
            if ob['start_date'] and ob['start_date'] > due_date:
                continue
            db.execute(
                'INSERT OR IGNORE INTO pending_obligations '
                '(obligation_id, due_date, status) VALUES (?,?,?)',
                (ob['id'], due_date, 'pending')
            )
        except (ValueError, TypeError):
            continue

    db.commit()


def ensure_pending_current_month(db, uid: int):
    """Called on page load — ensures this month's pending rows exist."""
    today = date_cls.today()
    generate_pending_for_month(db, uid, today.month, today.year)


# ── Routes ────────────────────────────────────────────────────────────────────

@obligations_bp.route('/obligations')
@login_required
def obligations():
    db  = get_db()
    uid = session['user_id']
    ensure_pending_current_month(db, uid)

    all_obs = db.execute(
        'SELECT * FROM obligations WHERE user_id=? ORDER BY status ASC, name ASC', (uid,)
    ).fetchall()

    m_exp, m_pct = get_monthly_spend()
    return render_template('obligations/obligations.html',
        obligations=all_obs,
        sidebar_expense=m_exp, sidebar_pct=m_pct,
    )


@obligations_bp.route('/obligations/add', methods=['POST'])
@login_required
def add_obligation():
    db  = get_db()
    uid = session['user_id']

    name      = request.form.get('name', '').strip()
    amount    = request.form.get('amount', '').strip()
    frequency = request.form.get('frequency', 'monthly').strip()
    due_day   = request.form.get('due_day', '1').strip() or '1'
    start     = request.form.get('start_date', '').strip()
    end       = request.form.get('end_date', '').strip() or None

    if not all([name, amount, start]):
        flash('Please fill all required fields.', 'error')
        return redirect(url_for('obligations_bp.obligations'))

    try:
        db.execute(
            'INSERT INTO obligations (user_id, name, amount, frequency, due_day, '
            'start_date, end_date, status, source_type) VALUES (?,?,?,?,?,?,?,?,?)',
            (uid, name, float(amount), frequency, int(due_day), start, end, 'active', 'manual')
        )
        db.commit()
        flash(f'Obligation "{name}" added.', 'success')
    except (ValueError, TypeError):
        flash('Invalid data.', 'error')

    return redirect(url_for('obligations_bp.obligations'))


@obligations_bp.route('/obligations/delete/<int:ob_id>', methods=['POST'])
@login_required
def delete_obligation(ob_id):
    db  = get_db()
    uid = session['user_id']
    ob  = db.execute('SELECT * FROM obligations WHERE id=? AND user_id=?', (ob_id, uid)).fetchone()
    if not ob:
        flash('Not found.', 'error')
        return redirect(url_for('obligations_bp.obligations'))
    db.execute('DELETE FROM pending_obligations WHERE obligation_id=?', (ob_id,))
    db.execute('DELETE FROM obligations WHERE id=? AND user_id=?', (ob_id, uid))
    db.commit()
    flash(f'Obligation "{ob["name"]}" deleted.', 'success')
    return redirect(url_for('obligations_bp.obligations'))


# ── Notification / pending payments API ──────────────────────────────────────

@obligations_bp.route('/obligations/api/pending')
@login_required
def api_pending():
    """JSON endpoint used by the notification bell."""
    db  = get_db()
    uid = session['user_id']
    ensure_pending_current_month(db, uid)

    rows = db.execute(
        '''SELECT po.id as pending_id, po.due_date, po.status,
                  ob.id as ob_id, ob.name, ob.amount, ob.source_type
           FROM pending_obligations po
           JOIN obligations ob ON ob.id = po.obligation_id
           WHERE ob.user_id=? AND po.status='pending'
           ORDER BY po.due_date ASC''',
        (uid,)
    ).fetchall()

    return jsonify([dict(r) for r in rows])


@obligations_bp.route('/obligations/approve/<int:pending_id>', methods=['POST'])
@login_required
def approve_pending(pending_id):
    """Approve a pending obligation — creates an expense transaction."""
    db  = get_db()
    uid = session['user_id']

    row = db.execute(
        '''SELECT po.*, ob.name, ob.amount, ob.source_type, ob.source_id, ob.id as ob_id
           FROM pending_obligations po
           JOIN obligations ob ON ob.id = po.obligation_id
           WHERE po.id=? AND ob.user_id=?''',
        (pending_id, uid)
    ).fetchone()

    if not row:
        return jsonify({'error': 'not found'}), 404

    # Pick the right category:
    # - Loan EMI → use the loan_type mapped to an expense category
    # - Manual recurring → 'Other'
    category = 'Other'
    if row['source_type'] == 'loan' and row['source_id']:
        loan = db.execute(
            'SELECT loan_type FROM loans WHERE id=?', (row['source_id'],)
        ).fetchone()
        if loan:
            loan_cat_map = {
                'Home Loan':        'Rent & Housing',
                'Car Loan':         'Transport',
                'Personal Loan':    'Other',
                'Education Loan':   'Education',
                'Credit Card Loan': 'Other',
                'Other':            'Other',
            }
            category = loan_cat_map.get(loan['loan_type'], 'Other')

    db.execute(
        'INSERT INTO txn (user_id, type, amount, category, description, date, '
        'source_type, source_id, is_recurring) VALUES (?,?,?,?,?,?,?,?,?)',
        (uid, 'expense', row['amount'], category,
         row['name'], row['due_date'],
         'obligation', row['ob_id'], 1)
    )
    db.execute(
        "UPDATE pending_obligations SET status='approved' WHERE id=?", (pending_id,)
    )
    db.commit()
    return jsonify({'ok': True, 'message': f'₹{row["amount"]:,.0f} recorded for {row["name"]}'})


@obligations_bp.route('/obligations/skip/<int:pending_id>', methods=['POST'])
@login_required
def skip_pending(pending_id):
    db  = get_db()
    uid = session['user_id']
    row = db.execute(
        '''SELECT po.* FROM pending_obligations po
           JOIN obligations ob ON ob.id = po.obligation_id
           WHERE po.id=? AND ob.user_id=?''',
        (pending_id, uid)
    ).fetchone()
    if not row:
        return jsonify({'error': 'not found'}), 404
    db.execute("UPDATE pending_obligations SET status='skipped' WHERE id=?", (pending_id,))
    db.commit()
    return jsonify({'ok': True})