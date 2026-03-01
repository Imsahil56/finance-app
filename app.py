import sqlite3
import os
from datetime import datetime, date
from functools import wraps
from flask import (Flask, render_template, redirect, url_for, request,
                   flash, session, g)
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = 'finance-analytics-secret-key-2024'

DATABASE = os.path.join(os.path.dirname(__file__), 'finance.db')

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
    db = sqlite3.connect(DATABASE)
    db.executescript('''
        CREATE TABLE IF NOT EXISTS user (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS txn (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            type TEXT NOT NULL,
            amount REAL NOT NULL,
            category TEXT NOT NULL,
            description TEXT,
            date DATE NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS monthly_budget (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            month INTEGER NOT NULL,
            year INTEGER NOT NULL,
            budget_amount REAL NOT NULL,
            UNIQUE(user_id, month, year)
        );
        CREATE TABLE IF NOT EXISTS category_budget (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            category TEXT NOT NULL,
            budget_amount REAL NOT NULL,
            UNIQUE(user_id, category)
        );
    ''')
    db.commit()
    db.close()

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            flash('Please log in.', 'info')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

def get_current_user():
    if 'user_id' not in session:
        return None
    return get_db().execute('SELECT * FROM user WHERE id=?', (session['user_id'],)).fetchone()

@app.context_processor
def inject_user():
    return {'current_user': get_current_user()}

EXPENSE_CATEGORIES = [
    'Food & Dining', 'Transport', 'Shopping', 'Entertainment',
    'Health & Medical', 'Utilities', 'Rent & Housing', 'Education',
    'Travel', 'Personal Care', 'Insurance', 'Subscriptions', 'Other'
]
INCOME_CATEGORIES = [
    'Salary', 'Freelance', 'Business', 'Investments',
    'Rental Income', 'Bonus', 'Gift', 'Other'
]

@app.route('/')
def index():
    return redirect(url_for('dashboard') if 'user_id' in session else url_for('login'))

@app.route('/register', methods=['GET', 'POST'])
def register():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        username = request.form.get('username','').strip()
        email = request.form.get('email','').strip()
        password = request.form.get('password','')
        confirm = request.form.get('confirm_password','')
        db = get_db()
        if not all([username, email, password, confirm]):
            flash('All fields are required.', 'error')
        elif password != confirm:
            flash('Passwords do not match.', 'error')
        elif db.execute('SELECT id FROM user WHERE username=?', (username,)).fetchone():
            flash('Username already taken.', 'error')
        elif db.execute('SELECT id FROM user WHERE email=?', (email,)).fetchone():
            flash('Email already registered.', 'error')
        else:
            db.execute('INSERT INTO user (username, email, password_hash) VALUES (?,?,?)',
                       (username, email, generate_password_hash(password)))
            db.commit()
            flash('Account created! Please log in.', 'success')
            return redirect(url_for('login'))
    return render_template('auth/register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        username = request.form.get('username','').strip()
        password = request.form.get('password','')
        db = get_db()
        user = db.execute('SELECT * FROM user WHERE username=?', (username,)).fetchone()
        if user and check_password_hash(user['password_hash'], password):
            session['user_id'] = user['id']
            session['username'] = user['username']
            return redirect(url_for('dashboard'))
        flash('Invalid username or password.', 'error')
    return render_template('auth/login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/dashboard')
@login_required
def dashboard():
    db = get_db()
    uid = session['user_id']
    now = datetime.now()
    month, year = now.month, now.year
    month_str = f'{month:02d}'

    rows = db.execute('SELECT type, amount FROM txn WHERE user_id=?', (uid,)).fetchall()
    total_income = sum(r['amount'] for r in rows if r['type'] == 'income')
    total_expense = sum(r['amount'] for r in rows if r['type'] == 'expense')
    balance = total_income - total_expense

    monthly_rows = db.execute(
        "SELECT type, category, amount FROM txn WHERE user_id=? AND strftime('%m', date)=? AND strftime('%Y', date)=?",
        (uid, month_str, str(year))
    ).fetchall()
    monthly_income = sum(r['amount'] for r in monthly_rows if r['type'] == 'income')
    monthly_expense = sum(r['amount'] for r in monthly_rows if r['type'] == 'expense')

    category_data = {}
    for r in monthly_rows:
        if r['type'] == 'expense':
            category_data[r['category']] = category_data.get(r['category'], 0) + r['amount']

    recent = db.execute(
        'SELECT * FROM txn WHERE user_id=? ORDER BY date DESC, created_at DESC LIMIT 5', (uid,)
    ).fetchall()

    monthly_budget = db.execute(
        'SELECT * FROM monthly_budget WHERE user_id=? AND month=? AND year=?', (uid, month, year)
    ).fetchone()

    monthly_budget_pct = 0
    if monthly_budget and monthly_budget['budget_amount']:
        monthly_budget_pct = min(round(monthly_expense / monthly_budget['budget_amount'] * 100), 100)

    cat_budgets = db.execute('SELECT * FROM category_budget WHERE user_id=?', (uid,)).fetchall()
    cat_budget_data = []
    for cb in cat_budgets:
        spent = category_data.get(cb['category'], 0)
        pct = min(round(spent / cb['budget_amount'] * 100), 100) if cb['budget_amount'] else 0
        cat_budget_data.append({
            'category': cb['category'], 'budget': cb['budget_amount'],
            'spent': spent, 'percent': pct, 'over': spent > cb['budget_amount']
        })
    
    # ── Income vs Expense by Month (fixed: unique labels across years) ──
    from collections import OrderedDict

    all_txn_rows = db.execute(
        "SELECT date, type, amount FROM txn WHERE user_id=? ORDER BY date ASC",
        (uid,)
    ).fetchall()

    monthly_data = OrderedDict()
    for row in all_txn_rows:
        month_key = row["date"][:7]   # YYYY-MM — unique per year+month
        if month_key not in monthly_data:
            dt = datetime.strptime(month_key, "%Y-%m")
            monthly_data[month_key] = {
                "label": dt.strftime("%b %y"),  # "Jan 25" — unique across years
                "income": 0.0,
                "expense": 0.0,
            }
        if row["type"] == "income":
            monthly_data[month_key]["income"] += row["amount"]
        else:
            monthly_data[month_key]["expense"] += row["amount"]

    # If only 1 month, use short label without year
    if len(monthly_data) == 1:
        for v in monthly_data.values():
            v["label"] = datetime.strptime(list(monthly_data.keys())[0], "%Y-%m").strftime("%b")

    income_expense_chart = list(monthly_data.values())

    return render_template('dashboard.html',
        total_income=total_income, total_expense=total_expense, balance=balance,
        monthly_income=monthly_income, monthly_expense=monthly_expense,
        category_data=category_data, recent=recent,
        monthly_budget=monthly_budget, monthly_budget_pct=monthly_budget_pct,
        cat_budget_data=cat_budget_data, month_name=now.strftime('%B %Y'),
        income_expense_chart=income_expense_chart
    )

@app.route('/transactions')
@login_required
def transactions():
    db  = get_db()
    uid = session['user_id']
    now = date.today()

    all_t = db.execute(
        'SELECT * FROM txn WHERE user_id=? ORDER BY date DESC, created_at DESC', (uid,)
    ).fetchall()

    # Monthly spend for sidebar widget
    monthly_rows = db.execute(
        'SELECT type, amount FROM txn WHERE user_id=? AND strftime("%m",date)=? AND strftime("%Y",date)=?',
        (uid, f'{now.month:02d}', str(now.year))
    ).fetchall()
    monthly_expense = sum(r['amount'] for r in monthly_rows if r['type'] == 'expense')

    monthly_budget = db.execute(
        'SELECT * FROM monthly_budget WHERE user_id=? AND month=? AND year=?',
        (uid, now.month, now.year)
    ).fetchone()
    monthly_budget_pct = 0
    if monthly_budget and monthly_budget['budget_amount']:
        monthly_budget_pct = min(round(monthly_expense / monthly_budget['budget_amount'] * 100), 100)

    return render_template('transaction/transactions.html',
        transactions=all_t,
        monthly_expense=monthly_expense,
        monthly_budget_pct=monthly_budget_pct
    )

@app.route('/add-transaction', methods=['GET', 'POST'])
@login_required
def add_transaction():
    if request.method == 'POST':
        t_type = request.form.get('type')
        amount = request.form.get('amount')
        category = request.form.get('category')
        description = request.form.get('description','').strip()
        t_date = request.form.get('date')
        if not all([t_type, amount, category, t_date]):
            flash('Please fill in all required fields.', 'error')
        else:
            try:
                get_db().execute(
                    'INSERT INTO txn (user_id, type, amount, category, description, date) VALUES (?,?,?,?,?,?)',
                    (session['user_id'], t_type, float(amount), category, description, t_date)
                )
                get_db().commit()
                flash('Transaction added!', 'success')
                return redirect(url_for('transactions'))
            except Exception as e:
                flash('Error saving transaction.', 'error')
    m_exp, m_pct = get_monthly_spend()
    return render_template('transaction/add_transaction.html',
        expense_categories=EXPENSE_CATEGORIES, income_categories=INCOME_CATEGORIES,
        today=date.today().isoformat(),
        monthly_expense=m_exp, monthly_budget_pct=m_pct
    )

@app.route('/edit-transaction/<int:id>', methods=['GET', 'POST'])
@login_required
def edit_transaction(id):
    db = get_db()
    t = db.execute('SELECT * FROM txn WHERE id=? AND user_id=?', (id, session['user_id'])).fetchone()
    if not t:
        flash('Transaction not found.', 'error')
        return redirect(url_for('transactions'))
    if request.method == 'POST':
        t_type = request.form.get('type')
        amount = request.form.get('amount')
        category = request.form.get('category')
        description = request.form.get('description','').strip()
        t_date = request.form.get('date')
        if not all([t_type, amount, category, t_date]):
            flash('Please fill in all required fields.', 'error')
        else:
            try:
                db.execute(
                    'UPDATE txn SET type=?, amount=?, category=?, description=?, date=? WHERE id=?',
                    (t_type, float(amount), category, description, t_date, id)
                )
                db.commit()
                flash('Transaction updated!', 'success')
                return redirect(url_for('transactions'))
            except Exception:
                flash('Error updating.', 'error')
    m_exp, m_pct = get_monthly_spend()
    return render_template('transaction/edit_transaction.html',
        transaction=t, expense_categories=EXPENSE_CATEGORIES, income_categories=INCOME_CATEGORIES,
        monthly_expense=m_exp, monthly_budget_pct=m_pct
    )

@app.route('/delete-transaction/<int:id>', methods=['POST'])
@login_required
def delete_transaction(id):
    db = get_db()
    db.execute('DELETE FROM txn WHERE id=? AND user_id=?', (id, session['user_id']))
    db.commit()
    flash('Transaction deleted.', 'success')
    return redirect(url_for('transactions'))

# ── redirect old URL so existing links don't break ──
@app.route('/set-budget')
@login_required
def set_budget():
    return redirect(url_for('budget'))

# ──────────────────────────────────────────────────────────────
# BUDGET LANDING
# ──────────────────────────────────────────────────────────────
@app.route('/budget')
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
    yearly_budget = db.execute(
        'SELECT * FROM monthly_budget WHERE user_id=? AND month=0 AND year=?',
        (uid, year)
    ).fetchone()

    monthly_rows = db.execute(
        "SELECT type, amount FROM txn WHERE user_id=? AND strftime('%m',date)=? AND strftime('%Y',date)=?",
        (uid, f'{month:02d}', str(year))
    ).fetchall()
    monthly_expense = sum(r['amount'] for r in monthly_rows if r['type'] == 'expense')
    monthly_income  = sum(r['amount'] for r in monthly_rows if r['type'] == 'income')

    yearly_rows = db.execute(
        "SELECT type, amount FROM txn WHERE user_id=? AND strftime('%Y',date)=?",
        (uid, str(year))
    ).fetchall()
    yearly_expense = sum(r['amount'] for r in yearly_rows if r['type'] == 'expense')

    monthly_budget_pct = 0
    if monthly_budget and monthly_budget['budget_amount']:
        monthly_budget_pct = min(round(monthly_expense / monthly_budget['budget_amount'] * 100), 100)

    yearly_budget_pct = 0
    if yearly_budget and yearly_budget['budget_amount']:
        yearly_budget_pct = min(round(yearly_expense / yearly_budget['budget_amount'] * 100), 100)

    cat_count = db.execute('SELECT COUNT(*) FROM category_budget WHERE user_id=?', (uid,)).fetchone()[0]
    total_allocated = db.execute(
        'SELECT COALESCE(SUM(budget_amount),0) FROM category_budget WHERE user_id=?', (uid,)
    ).fetchone()[0]

    m_exp, m_pct = get_monthly_spend()
    return render_template('budget/budget_landing.html',
        monthly_budget=monthly_budget, monthly_budget_pct=monthly_budget_pct,
        yearly_budget=yearly_budget, yearly_budget_pct=yearly_budget_pct,
        monthly_expense=monthly_expense, monthly_income=monthly_income,
        yearly_expense=yearly_expense,
        cat_count=cat_count, total_allocated=total_allocated,
        month_name=now.strftime('%B'), year=year,
        sidebar_expense=m_exp, sidebar_pct=m_pct
    )

# ──────────────────────────────────────────────────────────────
# MONTHLY BUDGET DETAIL
# ──────────────────────────────────────────────────────────────
@app.route('/budget/monthly', methods=['GET', 'POST'])
@login_required
def budget_monthly():
    db  = get_db()
    uid = session['user_id']
    now = datetime.now()

    try:
        sel_month = int(request.args.get('month', now.month))
        sel_year  = int(request.args.get('year',  now.year))
        if not (1 <= sel_month <= 12): raise ValueError
    except (ValueError, TypeError):
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
                    'INSERT INTO category_budget (user_id, category, budget_amount) VALUES (?,?,?) '
                    'ON CONFLICT(user_id, category) DO UPDATE SET budget_amount=excluded.budget_amount',
                    (uid, cat, float(amt))
                )
                db.commit()
                flash(f'Budget for {cat} set!', 'success')
        return redirect(url_for('budget_monthly', month=pm, year=py))

    monthly_budget = db.execute(
        'SELECT * FROM monthly_budget WHERE user_id=? AND month=? AND year=?',
        (uid, sel_month, sel_year)
    ).fetchone()

    monthly_rows = db.execute(
        "SELECT type, category, amount FROM txn WHERE user_id=? AND strftime('%m',date)=? AND strftime('%Y',date)=?",
        (uid, f'{sel_month:02d}', str(sel_year))
    ).fetchall()
    monthly_expense = sum(r['amount'] for r in monthly_rows if r['type'] == 'expense')
    monthly_income  = sum(r['amount'] for r in monthly_rows if r['type'] == 'income')

    cat_spend = {}
    for r in monthly_rows:
        if r['type'] == 'expense':
            cat_spend[r['category']] = cat_spend.get(r['category'], 0) + r['amount']

    category_budgets = db.execute('SELECT * FROM category_budget WHERE user_id=?', (uid,)).fetchall()
    cat_budget_data = []
    for cb in category_budgets:
        spent = cat_spend.get(cb['category'], 0)
        raw   = round(spent / cb['budget_amount'] * 100) if cb['budget_amount'] else 0
        cat_budget_data.append({
            'id': cb['id'], 'category': cb['category'],
            'budget': cb['budget_amount'], 'spent': spent,
            'pct': min(raw, 100), 'raw_pct': raw,
            'status': 'over' if raw >= 100 else ('warn' if raw >= 80 else 'ok')
        })

    monthly_budget_pct = 0
    if monthly_budget and monthly_budget['budget_amount']:
        monthly_budget_pct = min(round(monthly_expense / monthly_budget['budget_amount'] * 100), 100)

    # Month selector — last 12 months
    month_options = []
    for i in range(11, -1, -1):
        m = (now.month - i - 1) % 12 + 1
        y = now.year + ((now.month - i - 1) // 12)
        month_options.append({'month': m, 'year': y,
                               'label': datetime(y, m, 1).strftime('%b %Y')})

    m_exp, m_pct = get_monthly_spend()
    return render_template('budget/budget_monthly.html',
        monthly_budget=monthly_budget, monthly_budget_pct=monthly_budget_pct,
        monthly_expense=monthly_expense, monthly_income=monthly_income,
        cat_budget_data=cat_budget_data,
        sel_month=sel_month, sel_year=sel_year,
        sel_month_name=datetime(sel_year, sel_month, 1).strftime('%B %Y'),
        month_options=month_options,
        expense_categories=EXPENSE_CATEGORIES,
        sidebar_expense=m_exp, sidebar_pct=m_pct
    )

# ──────────────────────────────────────────────────────────────
# YEARLY BUDGET DETAIL
# ──────────────────────────────────────────────────────────────
@app.route('/budget/yearly', methods=['GET', 'POST'])
@login_required
def budget_yearly():
    db  = get_db()
    uid = session['user_id']
    now = datetime.now()

    try:
        sel_year = int(request.args.get('year', now.year))
    except (ValueError, TypeError):
        sel_year = now.year

    if request.method == 'POST':
        amt = request.form.get('budget_amount')
        py  = int(request.form.get('post_year', sel_year))
        if amt:
            db.execute(
                'INSERT INTO monthly_budget (user_id, month, year, budget_amount) VALUES (?,?,?,?) '
                'ON CONFLICT(user_id, month, year) DO UPDATE SET budget_amount=excluded.budget_amount',
                (uid, 0, py, float(amt))   # month=0 → yearly
            )
            db.commit()
            flash('Yearly budget saved!', 'success')
        return redirect(url_for('budget_yearly', year=py))

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

    cat_spend = {}
    for r in yearly_rows:
        if r['type'] == 'expense':
            cat_spend[r['category']] = cat_spend.get(r['category'], 0) + r['amount']

    category_budgets = db.execute('SELECT * FROM category_budget WHERE user_id=?', (uid,)).fetchall()
    cat_budget_data = []
    for cb in category_budgets:
        yearly_limit = cb['budget_amount'] * 12
        spent = cat_spend.get(cb['category'], 0)
        raw   = round(spent / yearly_limit * 100) if yearly_limit else 0
        cat_budget_data.append({
            'id': cb['id'], 'category': cb['category'],
            'budget': yearly_limit, 'monthly_budget': cb['budget_amount'], 'spent': spent,
            'pct': min(raw, 100), 'raw_pct': raw,
            'status': 'over' if raw >= 100 else ('warn' if raw >= 80 else 'ok')
        })

    yearly_budget_pct = 0
    if yearly_budget and yearly_budget['budget_amount']:
        yearly_budget_pct = min(round(yearly_expense / yearly_budget['budget_amount'] * 100), 100)

    year_options = list(range(now.year + 1, now.year - 4, -1))

    m_exp, m_pct = get_monthly_spend()
    return render_template('budget/budget_yearly.html',
        yearly_budget=yearly_budget, yearly_budget_pct=yearly_budget_pct,
        yearly_expense=yearly_expense, yearly_income=yearly_income,
        cat_budget_data=cat_budget_data,
        sel_year=sel_year, year_options=year_options,
        sidebar_expense=m_exp, sidebar_pct=m_pct
    )

# ──────────────────────────────────────────────────────────────
# EDIT CATEGORY BUDGET
# ──────────────────────────────────────────────────────────────
@app.route('/budget/category/<int:cb_id>', methods=['GET', 'POST'])
@login_required
def edit_category_budget(cb_id):
    db  = get_db()
    uid = session['user_id']
    cb  = db.execute('SELECT * FROM category_budget WHERE id=? AND user_id=?', (cb_id, uid)).fetchone()
    if not cb:
        flash('Budget not found.', 'error')
        return redirect(url_for('budget_monthly'))

    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'delete':
            db.execute('DELETE FROM category_budget WHERE id=? AND user_id=?', (cb_id, uid))
            db.commit()
            flash(f'Budget for {cb["category"]} removed.', 'success')
            return redirect(url_for('budget_monthly'))
        amt = request.form.get('budget_amount')
        if amt:
            db.execute('UPDATE category_budget SET budget_amount=? WHERE id=? AND user_id=?',
                       (float(amt), cb_id, uid))
            db.commit()
            flash(f'Budget for {cb["category"]} updated!', 'success')
            return redirect(url_for('budget_monthly'))

    now = datetime.now()
    spent_row = db.execute(
        "SELECT COALESCE(SUM(amount),0) as s FROM txn WHERE user_id=? AND category=? AND type='expense' "
        "AND strftime('%m',date)=? AND strftime('%Y',date)=?",
        (uid, cb['category'], f'{now.month:02d}', str(now.year))
    ).fetchone()
    spent = spent_row['s']
    pct   = min(round(spent / cb['budget_amount'] * 100), 100) if cb['budget_amount'] else 0

    m_exp, m_pct = get_monthly_spend()
    return render_template('budget/edit_category_budget.html',
        cb=cb, spent=spent, pct=pct,
        month_name=now.strftime('%B %Y'),
        sidebar_expense=m_exp, sidebar_pct=m_pct
    )

def get_monthly_spend():
    """Returns (monthly_expense, monthly_budget_pct) for the sidebar widget."""
    uid = session['user_id']
    now = date.today()
    rows = get_db().execute(
        'SELECT type, amount FROM txn WHERE user_id=? AND strftime("%m",date)=? AND strftime("%Y",date)=?',
        (uid, f'{now.month:02d}', str(now.year))
    ).fetchall()
    expense = sum(r['amount'] for r in rows if r['type'] == 'expense')
    budget  = get_db().execute(
        'SELECT budget_amount FROM monthly_budget WHERE user_id=? AND month=? AND year=?',
        (uid, now.month, now.year)
    ).fetchone()
    pct = 0
    if budget and budget['budget_amount']:
        pct = min(round(expense / budget['budget_amount'] * 100), 100)
    return expense, pct

@app.template_filter('strftime')
def strftime_filter(value, fmt):
    if isinstance(value, str):
        try:
            value = datetime.strptime(value, '%Y-%m-%d').date()
        except ValueError:
            return value
    return value.strftime(fmt)

if __name__ == '__main__':
    init_db()
    app.run(debug=True)