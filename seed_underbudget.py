"""
seed_underbudget.py
Demo profile: Under Budget — spends consistently below limits,
moderate income, no loans, healthy savings rate ~30%.

Usage:  python seed_underbudget.py

Creates user:
  Username : underbudget
  Password : demo123
"""

import os, sys, sqlite3, math, random
from datetime import date
from dateutil.relativedelta import relativedelta
from werkzeug.security import generate_password_hash

DATABASE = os.environ.get(
    'DATABASE_PATH',
    os.path.join(os.path.dirname(__file__), 'instance', 'app.db')
)

def get_db():
    if not os.path.exists(DATABASE):
        print(f"ERROR: Database not found at {DATABASE}")
        print("Run the app once first (python app.py) to create the DB.")
        sys.exit(1)
    db = sqlite3.connect(DATABASE)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys = ON')
    return db

def run():
    db = get_db()
    today = date.today()
    random.seed(99)

    # ── User ──────────────────────────────────────────────────────────────────
    existing = db.execute("SELECT id FROM user WHERE username='underbudget'").fetchone()
    if existing:
        uid = existing['id']
        print(f"User 'underbudget' exists (id={uid}). Clearing data...")
        for tbl in ['pending_obligations','obligations','loan_schedule','loans',
                    'txn','monthly_budget','category_budget',
                    'default_category_budget','monthly_category_budget']:
            try: db.execute(f'DELETE FROM {tbl} WHERE user_id=?', (uid,))
            except: pass
        db.execute('DELETE FROM pending_obligations WHERE obligation_id NOT IN (SELECT id FROM obligations)')
    else:
        cur = db.execute(
            'INSERT INTO user (username, email, password_hash) VALUES (?,?,?)',
            ('underbudget', 'underbudget@financeapp.com', generate_password_hash('demo123'))
        )
        uid = cur.lastrowid
        print(f"User 'underbudget' created (id={uid}).")

    db.commit()

    # ── Monthly budgets — generous, always under ──────────────────────────────
    # Budget is ₹60,000 but person spends ~₹38,000 — consistently under
    MONTHLY_BUDGET = 60000
    for i in range(-5, 1):
        d = today + relativedelta(months=i)
        db.execute(
            'INSERT OR IGNORE INTO monthly_budget (user_id,month,year,budget_amount) VALUES (?,?,?,?)',
            (uid, d.month, d.year, MONTHLY_BUDGET)
        )

    # ── Category budgets — all generous ──────────────────────────────────────
    cat_budgets = {
        'Food & Dining':    8000,
        'Transport':        5000,
        'Shopping':         8000,
        'Entertainment':    3000,
        'Health & Medical': 4000,
        'Utilities':        5000,
        'Rent & Housing':  15000,
        'Personal Care':    2000,
        'Subscriptions':    1500,
        'Other':            3000,
    }
    for cat, amt in cat_budgets.items():
        db.execute(
            'INSERT OR IGNORE INTO default_category_budget (user_id,category,amount) VALUES (?,?,?)',
            (uid, cat, amt)
        )

    # ── Transactions ──────────────────────────────────────────────────────────
    txns = []

    # Steady salary — ₹70,000/mo
    for i in range(-5, 1):
        d = today + relativedelta(months=i)
        txns.append(('income', 70000, 'Salary', 'Monthly Salary',
                      date(d.year, d.month, 1).isoformat(), 'manual', 1))

    # Small side income a few months
    for i in [-3, -1]:
        d = today + relativedelta(months=i)
        txns.append(('income', 8000, 'Freelance', 'Consulting fee',
                      date(d.year, d.month, 20).isoformat(), 'manual', 0))

    # Fixed recurring — paid past months only
    fixed = [
        (14000, 'Rent & Housing', 'House Rent',        1),
        (1099,  'Subscriptions',  'Netflix',            5),
        (499,   'Subscriptions',  'Spotify',            5),
    ]
    for i in range(-5, 0):
        d = today + relativedelta(months=i)
        for amt, cat, desc, day in fixed:
            txns.append(('expense', amt, cat, desc,
                          date(d.year, d.month, day).isoformat(), 'manual', 1))

    # Variable spending — WELL below budget in every category
    # Spends ~55-70% of each category limit
    templates = [
        # (mean_monthly, std, category, descriptions)
        (4500,  400, 'Food & Dining',    ['Home cooking supplies', 'Occasional takeout', 'Groceries', 'Weekly restaurant']),
        (2800,  300, 'Transport',        ['Monthly metro pass', 'Petrol', 'Occasional Uber']),
        (2500,  600, 'Shopping',         ['Amazon essentials', 'Clothing sale', 'Books']),
        (1200,  300, 'Entertainment',    ['Movie night', 'Board games', 'Streaming']),
        (1800,  400, 'Health & Medical', ['Pharmacy', 'Annual checkup', 'Vitamins']),
        (3200,  300, 'Utilities',        ['Electricity', 'Internet', 'Mobile recharge']),
        (900,   200, 'Personal Care',    ['Haircut', 'Grooming basics']),
        (700,   200, 'Other',            ['Household items', 'Small gifts']),
    ]
    for i in range(-5, 1):
        d = today + relativedelta(months=i)
        for base, std, cat, descs in templates:
            num = random.randint(2, 3)
            for _ in range(num):
                amt = max(100, round(random.gauss(base/num, std/num/2)))
                day = random.randint(1, 28)
                txns.append(('expense', amt, cat, random.choice(descs),
                              date(d.year, d.month, day).isoformat(), 'manual', 0))

    for t in txns:
        db.execute(
            'INSERT INTO txn (user_id,type,amount,category,description,date,source_type,is_recurring) '
            'VALUES (?,?,?,?,?,?,?,?)',
            (uid, t[0], t[1], t[2], t[3], t[4], t[5], t[6])
        )

    # ── No loans — this person is debt-free ───────────────────────────────────
    print("  ✓ No loans (debt-free profile)")

    # ── Commitments ───────────────────────────────────────────────────────────
    six_ago = (today + relativedelta(months=-6)).isoformat()
    commitments = [
        ('House Rent',        14000, 1,  'Rent & Housing'),
        ('Netflix',            1099, 5,  'Subscriptions'),
        ('Spotify',             499, 5,  'Subscriptions'),
        ('Internet Bill',      1000, 10, 'Utilities'),
        ('Mobile Recharge',     599, 1,  'Utilities'),
    ]
    for name, amt, due_day, cat in commitments:
        db.execute(
            'INSERT INTO obligations (user_id,name,amount,frequency,due_day,'
            'start_date,status,source_type,category) VALUES (?,?,?,?,?,?,?,?,?)',
            (uid, name, amt, 'monthly', due_day, six_ago, 'active', 'manual', cat)
        )

    db.commit()
    db.close()

    print("\n" + "="*55)
    print("  Under Budget profile seeded!")
    print("="*55)
    print("  Username : underbudget")
    print("  Password : demo123")
    print("="*55)
    print("\nProfile characteristics:")
    print("  • Income:  ₹70,000/mo salary + occasional freelance")
    print("  • Spend:   ~₹38,000/mo  (Budget: ₹60,000)")
    print("  • Savings: ~45% savings rate every month")
    print("  • Loans:   None (completely debt-free)")
    print("  • Budget:  All categories consistently under limit")
    print("  • Status:  GREEN — Healthy Finances")

if __name__ == '__main__':
    run()
