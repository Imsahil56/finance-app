"""
routes/db.py
Shared helpers imported by every blueprint.
Centralising these here means each blueprint no longer needs to
re-define get_db / login_required / get_monthly_spend locally.
"""

import sqlite3
from datetime import datetime, date
from functools import wraps

from flask import current_app, flash, g, redirect, session, url_for


# ── Database ──────────────────────────────────────────────────────────────────

def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(current_app.config['DATABASE'])
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys = ON')
    return db


# ── Auth guard ────────────────────────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            flash('Please log in.', 'info')
            return redirect(url_for('auth_bp.login'))
        return f(*args, **kwargs)
    return decorated


# ── Sidebar helper ────────────────────────────────────────────────────────────

def get_monthly_spend():
    """Return (monthly_expense, pct_of_budget) for the current month."""
    uid = session['user_id']
    now = date.today()
    db  = get_db()

    rows = db.execute(
        'SELECT type, amount FROM txn WHERE user_id=? '
        'AND strftime("%m", date)=? AND strftime("%Y", date)=?',
        (uid, f'{now.month:02d}', str(now.year))
    ).fetchall()
    expense = sum(r['amount'] for r in rows if r['type'] == 'expense')

    budget = db.execute(
        'SELECT budget_amount FROM monthly_budget '
        'WHERE user_id=? AND month=? AND year=?',
        (uid, now.month, now.year)
    ).fetchone()
    pct = 0
    if budget and budget['budget_amount']:
        pct = min(round(expense / budget['budget_amount'] * 100), 100)
    return expense, pct


# ── Category constants ────────────────────────────────────────────────────────

EXPENSE_CATEGORIES = [
    'Food & Dining', 'Transport', 'Shopping', 'Entertainment',
    'Health & Medical', 'Utilities', 'Rent & Housing', 'Education',
    'Travel', 'Personal Care', 'Insurance', 'Subscriptions', 'Other',
]

INCOME_CATEGORIES = [
    'Salary', 'Freelance', 'Business', 'Investments',
    'Rental Income', 'Bonus', 'Gift', 'Other',
]