"""
seed_saving_expert.py
Demo profile: Saving Expert — high income, minimal lifestyle spending,
aggressive savings rate ~60%, small car loan only, maxes every budget.

Usage:  python seed_saving_expert.py

Creates user:
  Username : savingexpert
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
        sys.exit(1)
    db = sqlite3.connect(DATABASE)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys = ON')
    return db

def calc_emi(principal, annual_rate, tenure_months):
    if annual_rate == 0:
        return round(principal/tenure_months, 2), 0.0, round(principal, 2)
    r = annual_rate / 12 / 100
    factor = math.pow(1+r, tenure_months)
    emi = principal * r * factor / (factor - 1)
    total = emi * tenure_months
    return round(emi, 2), round(total-principal, 2), round(total, 2)

def build_schedule(principal, annual_rate, tenure_months, emi, start_date):
    rows = []
    balance = principal
    r = annual_rate / 12 / 100
    for m in range(1, tenure_months+1):
        pay_date = start_date + relativedelta(months=m)
        if annual_rate == 0:
            interest_c, principal_c = 0.0, emi
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
    random.seed(777)

    # ── User ──────────────────────────────────────────────────────────────────
    existing = db.execute("SELECT id FROM user WHERE username='savingexpert'").fetchone()
    if existing:
        uid = existing['id']
        print(f"User 'savingexpert' exists (id={uid}). Clearing data...")
        for tbl in ['pending_obligations','obligations','loan_schedule','loans',
                    'txn','monthly_budget','category_budget',
                    'default_category_budget','monthly_category_budget']:
            try: db.execute(f'DELETE FROM {tbl} WHERE user_id=?', (uid,))
            except: pass
        db.execute('DELETE FROM pending_obligations WHERE obligation_id NOT IN (SELECT id FROM obligations)')
    else:
        cur = db.execute(
            'INSERT INTO user (username, email, password_hash) VALUES (?,?,?)',
            ('savingexpert', 'savingexpert@financeapp.com', generate_password_hash('demo123'))
        )
        uid = cur.lastrowid
        print(f"User 'savingexpert' created (id={uid}).")

    db.commit()

    # ── Budgets — tight & always under ───────────────────────────────────────
    # High income (₹1.8L/mo), self-imposed low budget (₹55,000), spends ₹40-45k
    MONTHLY_BUDGET = 55000
    for i in range(-5, 1):
        d = today + relativedelta(months=i)
        db.execute(
            'INSERT OR IGNORE INTO monthly_budget (user_id,month,year,budget_amount) VALUES (?,?,?,?)',
            (uid, d.month, d.year, MONTHLY_BUDGET)
        )

    cat_budgets = {
        'Food & Dining':    6000,
        'Transport':        4000,
        'Shopping':         5000,
        'Entertainment':    2000,
        'Health & Medical': 5000,
        'Utilities':        4000,
        'Rent & Housing':  18000,
        'Personal Care':    1500,
        'Subscriptions':    1000,
        'Investments':      8000,   # tracked as expense category
        'Other':            2000,
    }
    for cat, amt in cat_budgets.items():
        db.execute(
            'INSERT OR IGNORE INTO default_category_budget (user_id,category,amount) VALUES (?,?,?)',
            (uid, cat, amt)
        )

    # ── Transactions ──────────────────────────────────────────────────────────
    txns = []

    # High salary — ₹1,50,000/mo
    for i in range(-5, 1):
        d = today + relativedelta(months=i)
        txns.append(('income', 150000, 'Salary', 'Monthly Salary',
                      date(d.year, d.month, 1).isoformat(), 'manual', 1))

    # Regular investment returns / dividends
    for i in range(-5, 1):
        d = today + relativedelta(months=i)
        txns.append(('income', random.randint(8000,15000), 'Investment Returns',
                      'Dividend / MF returns',
                      date(d.year, d.month, 28).isoformat(), 'manual', 0))

    # Occasional freelance/bonus
    for i in [-5, -3, -1]:
        d = today + relativedelta(months=i)
        txns.append(('income', random.randint(20000,40000), 'Freelance',
                      'Consulting / Bonus',
                      date(d.year, d.month, 15).isoformat(), 'manual', 0))

    # Fixed recurring — past months
    fixed = [
        (17000, 'Rent & Housing', 'House Rent',    1),
        (499,   'Subscriptions',  'Netflix',        5),
        (299,   'Subscriptions',  'Spotify',        5),
    ]
    for i in range(-5, 0):
        d = today + relativedelta(months=i)
        for amt, cat, desc, day in fixed:
            txns.append(('expense', amt, cat, desc,
                          date(d.year, d.month, day).isoformat(), 'manual', 1))

    # Variable spending — very frugal, well under every category
    templates = [
        (3500, 300, 'Food & Dining',    ['Meal prep supplies', 'Groceries', 'Occasional lunch out']),
        (2000, 200, 'Transport',        ['Petrol', 'Metro pass', 'Occasional cab']),
        (1800, 400, 'Shopping',         ['Essential clothing', 'Books', 'Home supplies']),
        (800,  200, 'Entertainment',    ['Movie once a month', 'Streaming']),
        (2500, 400, 'Health & Medical', ['Gym membership', 'Supplements', 'Annual health check']),
        (2800, 200, 'Utilities',        ['Electricity', 'Internet', 'Mobile']),
        (900,  150, 'Personal Care',    ['Haircut', 'Basic grooming']),
        (6000, 500, 'Investments',      ['SIP — Nifty 50', 'PPF contribution', 'RD installment']),
        (500,  100, 'Other',            ['Household misc']),
    ]
    for i in range(-5, 1):
        d = today + relativedelta(months=i)
        for base, std, cat, descs in templates:
            num = random.randint(1, 3)
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

    # ── Single small car loan (low DTI ~5%) ───────────────────────────────────
    print("Adding loans...")
    start = date(today.year, today.month, 1) + relativedelta(months=-10)
    emi, ti, tp = calc_emi(400000, 8.0, 48)
    schedule = build_schedule(400000, 8.0, 48, emi, start)

    cur = db.execute(
        'INSERT INTO loans (user_id,loan_name,loan_type,loan_amount,interest_rate,'
        'tenure_months,start_date,monthly_emi,total_interest,total_payment,'
        'processing_fee,insurance,status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
        (uid,'Car Loan – HDFC','Car Loan',400000,8.0,48,
         start.isoformat(),emi,ti,tp,4000,0,'active')
    )
    loan_id = cur.lastrowid
    for row in schedule:
        db.execute(
            'INSERT INTO loan_schedule (loan_id,month_number,emi,interest_component,'
            'principal_component,remaining_balance,payment_date) VALUES (?,?,?,?,?,?,?)',
            (loan_id, row[0], row[1], row[2], row[3], row[4], row[5])
        )

    ob_cur = db.execute(
        'INSERT INTO obligations (user_id,name,amount,frequency,due_day,'
        'start_date,end_date,status,source_type,source_id,category) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
        (uid,'Car Loan EMI',emi,'monthly',1,
         start.isoformat(),
         (start+relativedelta(months=48)).isoformat(),
         'active','loan',loan_id,'Loan EMI')
    )
    ob_id = ob_cur.lastrowid

    for row in schedule:
        pay_date = row[5]
        if pay_date < date(today.year, today.month, 1).isoformat():
            db.execute(
                'INSERT INTO txn (user_id,type,amount,category,description,date,'
                'source_type,source_id,is_recurring) VALUES (?,?,?,?,?,?,?,?,?)',
                (uid,'expense',emi,'Loan EMI','Car Loan EMI',
                 pay_date,'obligation',ob_id,0)
            )
    print(f"  ✓ Car Loan — EMI ₹{emi:,.0f}/mo × 48 months")

    # ── Commitments ───────────────────────────────────────────────────────────
    six_ago = (today + relativedelta(months=-6)).isoformat()
    commitments = [
        ('House Rent',      17000, 1,  'Rent & Housing'),
        ('Netflix',           499, 5,  'Subscriptions'),
        ('Spotify',           299, 5,  'Subscriptions'),
        ('Internet',          999, 10, 'Utilities'),
        ('SIP — Nifty 50',   5000, 1,  'Investments'),
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
    print("  Saving Expert profile seeded!")
    print("="*55)
    print("  Username : savingexpert")
    print("  Password : demo123")
    print("="*55)
    print("\nProfile characteristics:")
    print("  • Income:  ₹1,50,000 salary + dividends + freelance")
    print("  • Spend:   ~₹42,000/mo  (Budget: ₹55,000)")
    print("  • Savings: ~60-70% savings rate every month")
    print("  • Loans:   1 small car loan  (DTI ~5%) — very healthy")
    print("  • Budget:  Well under all category limits")
    print("  • Investments tracked as expense category")
    print("  • Status:  GREEN — Excellent Health Score")

if __name__ == '__main__':
    run()
