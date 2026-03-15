"""
app.py
Application factory — config, DB lifecycle, template filters, blueprint wiring.
All route logic lives in routes/*.py
"""

import os
import sqlite3
from datetime import datetime

from flask import Flask, g, redirect, session, url_for

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'finance-analytics-secret-key-change-in-prod')

# ── Database config ───────────────────────────────────────────────────────────

DATABASE = os.path.join(os.path.dirname(__file__), 'instance', 'app.db')
app.config['DATABASE'] = DATABASE


def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(DATABASE)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys = ON')
    return db


@app.teardown_appcontext
def close_db(exc):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()


def init_db():
    """Create tables if they don't exist. Safe to call repeatedly."""
    os.makedirs(os.path.dirname(DATABASE), exist_ok=True)
    db = sqlite3.connect(DATABASE)
    db.executescript('''
        CREATE TABLE IF NOT EXISTS user (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            username      TEXT    UNIQUE NOT NULL,
            email         TEXT    UNIQUE NOT NULL,
            password_hash TEXT    NOT NULL,
            created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS txn (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL,
            type        TEXT    NOT NULL,
            amount      REAL    NOT NULL,
            category    TEXT    NOT NULL,
            description TEXT,
            date        DATE    NOT NULL,
            source_type TEXT    NOT NULL DEFAULT 'manual',
            source_id   INTEGER,
            is_recurring INTEGER NOT NULL DEFAULT 0,
            created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS monthly_budget (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id       INTEGER NOT NULL,
            month         INTEGER NOT NULL,
            year          INTEGER NOT NULL,
            budget_amount REAL    NOT NULL,
            UNIQUE(user_id, month, year)
        );
        CREATE TABLE IF NOT EXISTS category_budget (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id       INTEGER NOT NULL,
            category      TEXT    NOT NULL,
            budget_amount REAL    NOT NULL,
            UNIQUE(user_id, category)
        );
        CREATE TABLE IF NOT EXISTS default_category_budget (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id  INTEGER NOT NULL,
            category TEXT    NOT NULL,
            amount   REAL    NOT NULL,
            UNIQUE(user_id, category)
        );
        CREATE TABLE IF NOT EXISTS monthly_category_budget (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id  INTEGER NOT NULL,
            category TEXT    NOT NULL,
            month    INTEGER NOT NULL,
            year     INTEGER NOT NULL,
            amount   REAL    NOT NULL,
            UNIQUE(user_id, category, month, year)
        );
        CREATE TABLE IF NOT EXISTS loans (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id         INTEGER NOT NULL,
            loan_name       TEXT    NOT NULL,
            loan_type       TEXT    NOT NULL,
            loan_amount     REAL    NOT NULL,
            interest_rate   REAL    NOT NULL,
            tenure_months   INTEGER NOT NULL,
            start_date      DATE    NOT NULL,
            monthly_emi     REAL    NOT NULL,
            total_interest  REAL    NOT NULL,
            total_payment   REAL    NOT NULL,
            processing_fee  REAL    NOT NULL DEFAULT 0,
            insurance       REAL    NOT NULL DEFAULT 0,
            status          TEXT    NOT NULL DEFAULT 'active',
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS loan_schedule (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            loan_id             INTEGER NOT NULL,
            month_number        INTEGER NOT NULL,
            emi                 REAL    NOT NULL,
            interest_component  REAL    NOT NULL,
            principal_component REAL    NOT NULL,
            remaining_balance   REAL    NOT NULL,
            payment_date        DATE    NOT NULL,
            paid                INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS obligations (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL,
            name        TEXT    NOT NULL,
            amount      REAL    NOT NULL,
            frequency   TEXT    NOT NULL DEFAULT 'monthly',
            due_day     INTEGER NOT NULL DEFAULT 1,
            start_date  DATE    NOT NULL,
            end_date    DATE,
            status      TEXT    NOT NULL DEFAULT 'active',
            source_type TEXT    NOT NULL DEFAULT 'manual',
            source_id   INTEGER,
            category    TEXT    NOT NULL DEFAULT 'Other',
            created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS pending_obligations (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            obligation_id  INTEGER NOT NULL,
            due_date       DATE    NOT NULL,
            status         TEXT    NOT NULL DEFAULT 'pending',
            txn_id         INTEGER,
            created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(obligation_id, due_date)
        );
    ''')
    db.commit()
    db.close()


# ── Context processor ─────────────────────────────────────────────────────────

@app.context_processor
def inject_user():
    if 'user_id' not in session:
        return {'current_user': None}
    user = get_db().execute(
        'SELECT * FROM user WHERE id=?', (session['user_id'],)
    ).fetchone()
    return {'current_user': user}


# ── Template filters ──────────────────────────────────────────────────────────

@app.template_filter('strftime')
def strftime_filter(value, fmt):
    if isinstance(value, str):
        try:
            value = datetime.strptime(value, '%Y-%m-%d').date()
        except ValueError:
            return value
    return value.strftime(fmt)


# ── Root redirect ─────────────────────────────────────────────────────────────

@app.route('/')
def index():
    if 'user_id' in session:
        return redirect(url_for('dashboard_bp.dashboard'))
    return redirect(url_for('auth_bp.login'))


# ── Register blueprints ───────────────────────────────────────────────────────

from routes.auth         import auth_bp
from routes.dashboard    import dashboard_bp
from routes.transactions import transactions_bp
from routes.budget       import budget_bp
from routes.analysis     import analysis_bp
from routes.defaults     import defaults_bp
from routes.loans        import loans_bp
from routes.obligations  import obligations_bp

app.register_blueprint(auth_bp)
app.register_blueprint(dashboard_bp)
app.register_blueprint(transactions_bp)
app.register_blueprint(budget_bp)
app.register_blueprint(analysis_bp)
app.register_blueprint(defaults_bp)
app.register_blueprint(loans_bp)
app.register_blueprint(obligations_bp)


# ── Backwards-compat redirects ────────────────────────────────────────────────

@app.route('/set-budget')
def set_budget():
    return redirect(url_for('budget_bp.budget'))


# ── Always initialise DB on startup ──────────────────────────────────────────
# Runs regardless of how Flask is launched (flask run, python app.py, gunicorn…)
with app.app_context():
    init_db()
@app.route("/seed")
def seed():
    import seed_underbudget
    seed_underbudget.run()
    return "Database seeded!"

# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == '__main__':
    app.run(debug=True)