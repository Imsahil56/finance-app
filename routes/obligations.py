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
        'SELECT * FROM obligations WHERE user_id=? ORDER BY status ASC, source_type ASC, name ASC', (uid,)
    ).fetchall()

    # ── Summary stats ─────────────────────────────────────────────────────────
    today = date_cls.today()
    active_obs = [o for o in all_obs if o['status'] == 'active']
    total_monthly = sum(o['amount'] for o in active_obs if o['frequency'] == 'monthly')

    # Upcoming in next 7 days
    in_7 = today + timedelta(days=7)
    upcoming = db.execute(
        '''SELECT po.*, ob.name, ob.amount, ob.source_type
           FROM pending_obligations po
           JOIN obligations ob ON ob.id = po.obligation_id
           WHERE ob.user_id=? AND po.status='pending'
             AND po.due_date >= ? AND po.due_date <= ?
           ORDER BY po.due_date ASC''',
        (uid, today.isoformat(), in_7.isoformat())
    ).fetchall()
    upcoming_total  = sum(r['amount'] for r in upcoming)
    upcoming_names  = ', '.join(r['name'] for r in upcoming[:3])
    if len(upcoming) > 3:
        upcoming_names += f' +{len(upcoming)-3} more'

    # Loan installments
    loan_obs = [o for o in active_obs if o['source_type'] == 'loan']
    loan_emi_total = sum(o['amount'] for o in loan_obs)

    # Icon + color map by category / source
    CATEGORY_ICONS = {
        'Rent & Housing': ('home',             '#6366f1'),
        'Transport':      ('directions_car',   '#f59e0b'),
        'Education':      ('school',           '#10b981'),
        'Health & Medical':('favorite',        '#ec4899'),
        'Utilities':      ('bolt',             '#f97316'),
        'Subscriptions':  ('subscriptions',    '#8b5cf6'),
        'Food & Dining':  ('restaurant',       '#ef4444'),
        'Loan EMI':       ('account_balance',  '#2094f3'),
        'Other':          ('receipt_long',     '#64748b'),
    }

    enriched = []
    for ob in all_obs:
        d = dict(ob)
        cat   = d.get('category') or 'Other'
        meta  = CATEGORY_ICONS.get(cat, CATEGORY_ICONS['Other'])
        if ob['source_type'] == 'loan':
            meta = ('account_balance', '#2094f3')
        d['icon']  = meta[0]
        d['color'] = meta[1]
        enriched.append(d)

    # ── Subscription burn rate (last 6 months) ───────────────────────────────
    from dateutil.relativedelta import relativedelta
    burn_rate = []
    for i in range(5, -1, -1):
        d = today + relativedelta(months=-i)
        s = db.execute(
            "SELECT COALESCE(SUM(amount),0) as s FROM txn "
            "WHERE user_id=? AND type='expense' AND category='Subscriptions' "
            "AND strftime('%m',date)=? AND strftime('%Y',date)=?",
            (uid, f'{d.month:02d}', str(d.year))
        ).fetchone()['s']
        burn_rate.append({'label': d.strftime('%b'), 'amount': round(s)})

    # ── Fixed costs vs subscriptions breakdown ────────────────────────────────
    sub_categories = {'Subscriptions'}
    fixed_costs_total = sum(o['amount'] for o in active_obs
                            if o['frequency'] == 'monthly'
                            and (o['category'] or 'Other') not in sub_categories)
    subs_total = sum(o['amount'] for o in active_obs
                     if o['frequency'] == 'monthly'
                     and (o['category'] or 'Other') in sub_categories)

    # ── Upcoming due soon (next 14 days) ─────────────────────────────────────
    in_14 = today + timedelta(days=14)
    due_soon = db.execute(
        '''SELECT po.id as pending_id, po.due_date, ob.name, ob.amount,
                  ob.category, ob.source_type
           FROM pending_obligations po
           JOIN obligations ob ON ob.id = po.obligation_id
           WHERE ob.user_id=? AND po.status='pending'
             AND po.due_date >= ? AND po.due_date <= ?
           ORDER BY po.due_date ASC LIMIT 4''',
        (uid, today.isoformat(), in_14.isoformat())
    ).fetchall()

    # Days away labels
    due_soon_enriched = []
    for r in due_soon:
        dr = dict(r)
        diff = (date_cls.fromisoformat(r['due_date']) - today).days
        dr['days_label'] = 'Today' if diff == 0 else ('Tomorrow' if diff == 1 else f'Due in {diff} days')
        dr['urgent'] = diff <= 2
        due_soon_enriched.append(dr)

    m_exp, m_pct = get_monthly_spend()
    return render_template('obligations/obligations.html',
        obligations=enriched,
        total_monthly=total_monthly,
        fixed_costs_total=fixed_costs_total,
        subs_total=subs_total,
        upcoming_total=upcoming_total,
        upcoming_names=upcoming_names,
        upcoming_count=len(upcoming),
        loan_emi_total=loan_emi_total,
        loan_count=len(loan_obs),
        active_count=len(active_obs),
        due_soon=due_soon_enriched,
        burn_rate=burn_rate,
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
    category  = request.form.get('category', 'Other').strip()

    if not all([name, amount, start]):
        flash('Please fill all required fields.', 'error')
        return redirect(url_for('obligations_bp.obligations'))

    try:
        db.execute(
            'INSERT INTO obligations (user_id, name, amount, frequency, due_day, '
            'start_date, end_date, status, source_type, category) VALUES (?,?,?,?,?,?,?,?,?,?)',
            (uid, name, float(amount), frequency, int(due_day), start, end, 'active', 'manual', category)
        )
        db.commit()
        flash(f'Commitment "{name}" added.', 'success')
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

    # Use the category stored on the obligation itself.
    # Loan obligations store 'Loan EMI'; manual ones store their own category.
    # Fall back to 'Other' if somehow missing.
    ob_full = db.execute(
        'SELECT category FROM obligations WHERE id=?', (row['ob_id'],)
    ).fetchone()
    category = (ob_full['category'] if ob_full and ob_full['category'] else 'Other')

    # Guard: don't insert if a transaction already exists for this
    # obligation + due_date (prevents double-entry if seeded data overlaps)
    existing = db.execute(
        '''SELECT id FROM txn
           WHERE user_id=? AND source_type IN ('obligation','loan')
           AND source_id=? AND date=? AND amount=?''',
        (uid, row['ob_id'], row['due_date'], row['amount'])
    ).fetchone()
    if existing:
        db.execute("UPDATE pending_obligations SET status='approved' WHERE id=?", (pending_id,))
        db.commit()
        return jsonify({'ok': True, 'message': f'Already recorded for {row["name"]}'})

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


@obligations_bp.route('/obligations/export-csv')
@login_required
def export_csv():
    """Export active obligations as CSV."""
    import csv, io
    db  = get_db()
    uid = session['user_id']
    obs = db.execute(
        "SELECT name, amount, frequency, due_day, category, status, start_date "
        "FROM obligations WHERE user_id=? ORDER BY status, amount DESC",
        (uid,)
    ).fetchall()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Name', 'Amount', 'Frequency', 'Due Day', 'Category', 'Status', 'Start Date'])
    for o in obs:
        writer.writerow([o['name'], o['amount'], o['frequency'], o['due_day'],
                         o['category'], o['status'], o['start_date']])

    from flask import Response
    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': 'attachment; filename=commitments.csv'}
    )