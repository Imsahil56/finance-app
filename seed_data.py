"""
seed_data.py
Populates the database with realistic sample data for testing.

Usage:
    python seed_data.py

Creates a demo user:
    Username : demo
    Password : demo123

Safe to run multiple times — deletes existing demo user data first.
"""

import os
import sys
import sqlite3
import math
from datetime import date, datetime
from dateutil.relativedelta import relativedelta
from werkzeug.security import generate_password_hash

# ── DB path (mirrors app.py logic) ───────────────────────────────────────────
DATABASE = os.environ.get(
    'DATABASE_PATH',
    os.path.join(os.path.dirname(__file__), 'instance', 'app.db')
)


def get_db():
    if not os.path.exists(DATABASE):
        print(f"ERROR: Database not found at {DATABASE}")
        print("Run the app once first (python app.py) so the DB is created.")
        sys.exit(1)
    db = sqlite3.connect(DATABASE)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys = ON')
    return db


# ── EMI helpers ───────────────────────────────────────────────────────────────
def calc_emi(principal, annual_rate, tenure_months):
    if annual_rate == 0:
        emi = principal / tenure_months
        return round(emi, 2), 0.0, round(principal, 2)
    r = annual_rate / 12 / 100
    factor = math.pow(1 + r, tenure_months)
    emi = principal * r * factor / (factor - 1)
    total = emi * tenure_months
    return round(emi, 2), round(total - principal, 2), round(total, 2)


def build_schedule(principal, annual_rate, tenure_months, emi, start_date):
    rows = []
    balance = principal
    r = annual_rate / 12 / 100
    for m in range(1, tenure_months + 1):
        pay_date = start_date + relativedelta(months=m)
        if annual_rate == 0:
            interest_c = 0.0
            principal_c = emi
        else:
            interest_c = round(balance * r, 2)
            principal_c = round(emi - interest_c, 2)
        if m == tenure_months:
            principal_c = round(balance, 2)
        balance = max(0.0, round(balance - principal_c, 2))
        rows.append((m, emi, interest_c, principal_c, balance, pay_date.isoformat()))
    return rows


def run():
    db = get_db()
    today = date.today()

    # ── 1. Create / reset demo user ───────────────────────────────────────────
    existing = db.execute("SELECT id FROM user WHERE username='demo'").fetchone()
    if existing:
        uid = existing['id']
        print(f"Demo user exists (id={uid}). Clearing existing data...")
        for tbl in ['pending_obligations', 'obligations', 'loan_schedule',
                    'loans', 'txn', 'monthly_budget', 'category_budget',
                    'default_category_budget', 'monthly_category_budget']:
            try:
                db.execute(f'DELETE FROM {tbl} WHERE user_id=?', (uid,))
            except Exception:
                pass
        # pending_obligations has no user_id — delete via obligation_id
        db.execute('''
            DELETE FROM pending_obligations WHERE obligation_id NOT IN
            (SELECT id FROM obligations)
        ''')
    else:
        cur = db.execute(
            'INSERT INTO user (username, email, password_hash) VALUES (?,?,?)',
            ('demo', 'demo@financeapp.com', generate_password_hash('demo123'))
        )
        uid = cur.lastrowid
        print(f"Demo user created (id={uid}).")

    db.commit()

    # ── 2. Monthly budgets (last 6 months + current) ──────────────────────────
    print("Adding monthly budgets...")
    for i in range(-5, 1):
        d = today + relativedelta(months=i)
        db.execute(
            'INSERT OR IGNORE INTO monthly_budget (user_id, month, year, budget_amount) VALUES (?,?,?,?)',
            (uid, d.month, d.year, 75000)
        )

    # ── 3. Default category budgets ───────────────────────────────────────────
    cat_budgets = {
        'Food & Dining': 12000,
        'Transport': 8000,
        'Shopping': 10000,
        'Entertainment': 4000,
        'Health & Medical': 5000,
        'Utilities': 6000,
        'Rent & Housing': 20000,
        'Education': 5000,
        'Loan EMI': 30000,
        'Personal Care': 3000,
        'Subscriptions': 2000,
        'Other': 5000,
    }
    for cat, amt in cat_budgets.items():
        db.execute(
            'INSERT OR IGNORE INTO default_category_budget (user_id, category, amount) VALUES (?,?,?)',
            (uid, cat, amt)
        )

    # ── 4. Transactions (6 months of history) ────────────────────────────────
    print("Adding transactions...")
    txns = []

    # Salary — every month
    for i in range(-5, 1):
        d = today + relativedelta(months=i)
        salary_date = date(d.year, d.month, 1).isoformat()
        txns.append(('income', 85000, 'Salary', 'Monthly Salary', salary_date, 'manual', 1))

    # Freelance — some months
    for i in [-4, -2, 0]:
        d = today + relativedelta(months=i)
        txns.append(('income', 15000, 'Freelance', 'Freelance Project', date(d.year, d.month, 15).isoformat(), 'manual', 0))

    # Recurring expenses — past months only
    # Current month's recurring items should come via the Commitments (obligations) system.
    # Seeding past months gives historical data without conflicting with this month's obligations.
    recurring_expenses = [
        (20000, 'Rent & Housing', 'House Rent', 1),
        (1299,  'Subscriptions',  'Netflix + Spotify', 5),
        (800,   'Subscriptions',  'AWS / Hosting', 5),
    ]
    for i in range(-5, 0):  # -5 to -1: past 5 months only, skip current month
        d = today + relativedelta(months=i)
        for amt, cat, desc, day in recurring_expenses:
            txns.append(('expense', amt, cat, desc, date(d.year, d.month, day).isoformat(), 'manual', 1))

    # Variable expenses — realistic monthly spending
    import random
    random.seed(42)
    variable_templates = [
        (4500,  800,  'Food & Dining',   ['Zomato', 'Swiggy', 'Groceries', 'Restaurant dinner']),
        (3500,  600,  'Transport',       ['Uber/Ola', 'Petrol', 'Metro card recharge']),
        (3000, 1500,  'Shopping',        ['Amazon', 'Myntra', 'Flipkart', 'Electronics']),
        (1500,  500,  'Entertainment',   ['Movie tickets', 'OTT subscription', 'Weekend outing']),
        (2000,  800,  'Health & Medical',['Pharmacy', 'Doctor consultation', 'Lab tests']),
        (2500,  400,  'Utilities',       ['Electricity bill', 'Internet bill', 'Mobile recharge', 'Water bill']),
        (1200,  300,  'Personal Care',   ['Salon', 'Grooming', 'Cosmetics']),
        (1000,  400,  'Other',           ['Miscellaneous', 'Birthday gift', 'Household items']),
    ]
    for i in range(-5, 1):
        d = today + relativedelta(months=i)
        for base_amt, variance, cat, descs in variable_templates:
            # 2-4 transactions per category per month
            num = random.randint(2, 4)
            for _ in range(num):
                amt = round(random.gauss(base_amt / num, variance / num / 2))
                amt = max(100, amt)
                day = random.randint(1, 28)
                desc = random.choice(descs)
                txns.append(('expense', amt, cat, desc,
                              date(d.year, d.month, day).isoformat(), 'manual', 0))

    for t in txns:
        db.execute(
            'INSERT INTO txn (user_id, type, amount, category, description, date, source_type, is_recurring) '
            'VALUES (?,?,?,?,?,?,?,?)',
            (uid, t[0], t[1], t[2], t[3], t[4], t[5], t[6])
        )

    # ── 5. Loans ──────────────────────────────────────────────────────────────
    print("Adding loans...")

    loans_data = [
        # (name, type, amount, rate, tenure_months, start_date_offset_months, proc_fee, insurance)
        ('Home Loan – SBI',         'Home Loan',             4500000, 8.5,  240, -36, 45000, 20000),
        ('Car Loan – HDFC',         'Car Loan',               650000, 9.0,   60, -18,  6500,     0),
        ('Personal Loan – ICICI',   'Personal Loan',          200000, 13.5,  36,  -8,  3000,     0),
        ('Education Loan – Axis',   'Education Loan',         800000, 7.5,   84, -24, 8000,      0),
    ]

    for loan_name, loan_type, principal, rate, tenure, start_offset, proc_fee, insurance in loans_data:
        start_date = today + relativedelta(months=start_offset)
        start_date = date(start_date.year, start_date.month, 1)

        emi, total_interest, total_payment = calc_emi(principal, rate, tenure)
        schedule = build_schedule(principal, rate, tenure, emi, start_date)

        cur = db.execute(
            'INSERT INTO loans (user_id, loan_name, loan_type, loan_amount, interest_rate, '
            'tenure_months, start_date, monthly_emi, total_interest, total_payment, '
            'processing_fee, insurance, status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (uid, loan_name, loan_type, principal, rate, tenure,
             start_date.isoformat(), emi, total_interest, total_payment,
             proc_fee, insurance, 'active')
        )
        loan_id = cur.lastrowid

        # Insert amortization schedule
        for row in schedule:
            db.execute(
                'INSERT INTO loan_schedule (loan_id, month_number, emi, interest_component, '
                'principal_component, remaining_balance, payment_date) VALUES (?,?,?,?,?,?,?)',
                (loan_id, row[0], row[1], row[2], row[3], row[4], row[5])
            )

        # Create EMI obligation
        due_day = min(start_date.day, 28)
        end_date = (start_date + relativedelta(months=tenure)).isoformat()
        ob_cur = db.execute(
            'INSERT INTO obligations (user_id, name, amount, frequency, due_day, '
            'start_date, end_date, status, source_type, source_id, category) '
            'VALUES (?,?,?,?,?,?,?,?,?,?,?)',
            (uid, f'{loan_name} EMI', emi, 'monthly', due_day,
             start_date.isoformat(), end_date, 'active', 'loan', loan_id, 'Loan EMI')
        )
        obligation_id = ob_cur.lastrowid

        # Add past EMI transactions to txn table (past months only)
        # Current month's EMI should come from approving the pending obligation.
        # source_id = obligation_id so the duplicate guard in approve_pending works.
        for row in schedule:
            pay_date = row[5]
            if pay_date < date(today.year, today.month, 1).isoformat():
                db.execute(
                    'INSERT INTO txn (user_id, type, amount, category, description, date, source_type, source_id, is_recurring) '
                    'VALUES (?,?,?,?,?,?,?,?,?)',
                    (uid, 'expense', emi, 'Loan EMI', f'{loan_name} EMI', pay_date, 'obligation', obligation_id, 0)
                )

        print(f"  ✓ {loan_name} — EMI ₹{emi:,.0f}/mo × {tenure} months")

    # ── 6. A closed loan ─────────────────────────────────────────────────────
    old_start = date(today.year - 3, today.month, 1)
    emi_c, ti_c, tp_c = calc_emi(150000, 11.0, 24)
    schedule_c = build_schedule(150000, 11.0, 24, emi_c, old_start)
    cur = db.execute(
        'INSERT INTO loans (user_id, loan_name, loan_type, loan_amount, interest_rate, '
        'tenure_months, start_date, monthly_emi, total_interest, total_payment, '
        'processing_fee, insurance, status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
        (uid, 'Consumer Loan – Bajaj', 'Consumer Durable Loan', 150000, 11.0, 24,
         old_start.isoformat(), emi_c, ti_c, tp_c, 1500, 0, 'closed')
    )
    closed_loan_id = cur.lastrowid
    for row in schedule_c:
        db.execute(
            'INSERT INTO loan_schedule (loan_id, month_number, emi, interest_component, '
            'principal_component, remaining_balance, payment_date) VALUES (?,?,?,?,?,?,?)',
            (closed_loan_id, row[0], row[1], row[2], row[3], row[4], row[5])
        )
    print(f"  ✓ Consumer Loan – Bajaj (CLOSED)")

    # ── 7. Manual obligations (commitments) ───────────────────────────────────
    print("Adding commitments...")
    commitments = [
        ('House Rent',            20000, 1,  'Rent & Housing'),
        ('Gym Membership',         2500, 5,  'Health & Medical'),
        ('Netflix + Spotify',      1299, 5,  'Subscriptions'),
        ('Electricity Bill',       2200, 15, 'Utilities'),
        ('Internet Bill',          1199, 10, 'Utilities'),
    ]
    six_months_ago = (today + relativedelta(months=-6)).isoformat()
    for name, amt, due_day, cat in commitments:
        db.execute(
            'INSERT INTO obligations (user_id, name, amount, frequency, due_day, '
            'start_date, status, source_type, category) VALUES (?,?,?,?,?,?,?,?,?)',
            (uid, name, amt, 'monthly', due_day, six_months_ago, 'active', 'manual', cat)
        )

    # ── 8. Commit everything ──────────────────────────────────────────────────
    db.commit()
    db.close()

    print("\n" + "="*55)
    print("  Sample data seeded successfully!")
    print("="*55)
    print(f"  URL      : http://localhost:5000")
    print(f"  Username : demo")
    print(f"  Password : demo123")
    print("="*55)
    print("\nWhat's included:")
    print("  • 6 months of income (salary + freelance)")
    print("  • 6 months of realistic expense transactions")
    print("  • 4 active loans (Home, Car, Personal, Education)")
    print("  • 1 closed loan (Consumer Durable)")
    print("  • 5 monthly commitments (rent, gym, Netflix, bills)")
    print("  • Monthly budgets set to ₹75,000")


if __name__ == '__main__':
    run()