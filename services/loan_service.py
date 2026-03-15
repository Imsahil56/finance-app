"""
services/loan_service.py
All loan business logic: EMI calculation, amortization, extra payments,
balance tracking, stats, insights.
"""

import math
from datetime import date as date_cls, datetime
from dateutil.relativedelta import relativedelta


# ── Loan type config ──────────────────────────────────────────────────────────

LOAN_TYPES = [
    'Home Loan',
    'Car Loan',
    'Personal Loan',
    'Education Loan',
    'Credit Card EMI',
    'Consumer Durable Loan',
    'Business Loan',
    'Other',
]

LOAN_TYPE_META = {
    'Home Loan':             {'icon': 'home',              'color': 'blue'},
    'Car Loan':              {'icon': 'directions_car',    'color': 'amber'},
    'Personal Loan':         {'icon': 'person',            'color': 'purple'},
    'Education Loan':        {'icon': 'school',            'color': 'emerald'},
    'Credit Card EMI':       {'icon': 'credit_card',       'color': 'rose'},
    'Consumer Durable Loan': {'icon': 'devices',           'color': 'cyan'},
    'Business Loan':         {'icon': 'business_center',   'color': 'orange'},
    'Other':                 {'icon': 'receipt_long',      'color': 'slate'},
}

# Obligation category mapping by loan type
LOAN_EMI_CATEGORY = 'Loan EMI'

LOAN_TYPE_TO_CATEGORY = {
    'Home Loan':             'Rent & Housing',
    'Car Loan':              'Transport',
    'Personal Loan':         LOAN_EMI_CATEGORY,
    'Education Loan':        'Education',
    'Credit Card EMI':       LOAN_EMI_CATEGORY,
    'Consumer Durable Loan': LOAN_EMI_CATEGORY,
    'Business Loan':         LOAN_EMI_CATEGORY,
    'Other':                 LOAN_EMI_CATEGORY,
}


def get_loan_meta(loan_type: str) -> dict:
    return LOAN_TYPE_META.get(loan_type, LOAN_TYPE_META['Other'])


# ── EMI & amortization ────────────────────────────────────────────────────────

def calculate_emi(principal: float, annual_rate: float, tenure_months: int) -> dict:
    """Standard reducing-balance EMI formula."""
    if annual_rate == 0:
        emi = principal / tenure_months
        total_payment = principal
        total_interest = 0.0
    else:
        r = annual_rate / 12 / 100
        factor = math.pow(1 + r, tenure_months)
        emi = principal * r * factor / (factor - 1)
        total_payment = emi * tenure_months
        total_interest = total_payment - principal
    return {
        'monthly_emi':    round(emi, 2),
        'total_interest': round(total_interest, 2),
        'total_payment':  round(total_payment, 2),
    }


def build_amortization(principal: float, annual_rate: float,
                       tenure_months: int, monthly_emi: float,
                       start_date) -> list:
    """Generate full amortization schedule from scratch."""
    schedule = []
    balance = principal
    r = annual_rate / 12 / 100
    for month in range(1, tenure_months + 1):
        pay_date = start_date + relativedelta(months=month)
        if annual_rate == 0:
            interest_component = 0.0
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


def rebuild_schedule_from(db, loan_id: int, from_month: int,
                          current_balance: float, annual_rate: float,
                          monthly_emi: float, base_date) -> list:
    """
    Rebuild remaining schedule after an extra payment.
    Deletes future rows and regenerates from from_month with the new balance.
    Returns (new_schedule, total_interest_remaining, months_remaining).
    """
    # Delete all future rows
    db.execute(
        'DELETE FROM loan_schedule WHERE loan_id=? AND month_number>=?',
        (loan_id, from_month)
    )

    balance = current_balance
    r = annual_rate / 12 / 100
    new_rows = []
    month = from_month
    while balance > 0.01:
        pay_date = base_date + relativedelta(months=month)
        if annual_rate == 0:
            interest_component  = 0.0
            principal_component = min(monthly_emi, balance)
        else:
            interest_component  = round(balance * r, 2)
            principal_component = round(monthly_emi - interest_component, 2)
            if principal_component <= 0:
                principal_component = round(balance, 2)
        if balance - principal_component < 0.01:
            principal_component = round(balance, 2)
        balance = max(0.0, round(balance - principal_component, 2))
        new_rows.append({
            'loan_id':             loan_id,
            'month_number':        month,
            'emi':                 monthly_emi,
            'interest_component':  interest_component,
            'principal_component': principal_component,
            'remaining_balance':   balance,
            'payment_date':        pay_date.isoformat(),
        })
        month += 1
        if month > 1200:  # safety cap (100 years)
            break

    for row in new_rows:
        db.execute(
            'INSERT INTO loan_schedule (loan_id, month_number, emi, interest_component, '
            'principal_component, remaining_balance, payment_date) VALUES (?,?,?,?,?,?,?)',
            (loan_id, row['month_number'], row['emi'], row['interest_component'],
             row['principal_component'], row['remaining_balance'], row['payment_date'])
        )

    total_interest_remaining = sum(r['interest_component'] for r in new_rows)
    return new_rows, total_interest_remaining, len(new_rows)


# ── Balance & enrichment ──────────────────────────────────────────────────────

def get_current_balance(db, loan_id: int, loan_amount: float) -> float:
    """Get remaining balance from the schedule (latest past payment date)."""
    today = date_cls.today().isoformat()
    row = db.execute(
        'SELECT remaining_balance FROM loan_schedule '
        'WHERE loan_id=? AND payment_date<=? ORDER BY month_number DESC LIMIT 1',
        (loan_id, today)
    ).fetchone()
    return row['remaining_balance'] if row else loan_amount


def get_next_unpaid_month(db, loan_id: int) -> int:
    """Return the month_number of the next upcoming payment."""
    today = date_cls.today().isoformat()
    row = db.execute(
        'SELECT month_number FROM loan_schedule '
        'WHERE loan_id=? AND payment_date>? ORDER BY month_number ASC LIMIT 1',
        (loan_id, today)
    ).fetchone()
    return row['month_number'] if row else None


def enrich_loan(db, d: dict) -> dict:
    """Add computed display fields to a loan dict."""
    amount    = d.get('loan_amount') or 0
    remaining = d.get('remaining_balance', amount)
    paid      = max(0.0, amount - remaining)
    d['paid_amount'] = paid
    d['paid_pct']    = min(round(paid / amount * 100) if amount else 0, 100)

    meta = get_loan_meta(d.get('loan_type', 'Other'))
    d['type_icon']  = meta['icon']
    d['type_color'] = meta['color']

    try:
        start = datetime.strptime(d['start_date'], '%Y-%m-%d').date()
        end   = start + relativedelta(months=d['tenure_months'])
        today = date_cls.today()
        total = d['tenure_months']
        done  = max(0, (today.year - start.year) * 12 + (today.month - start.month))
        done  = min(done, total)
        d['end_date']         = end.isoformat()
        d['end_date_display'] = end.strftime('%b %Y')
        d['completed_months'] = done
        d['remaining_months'] = max(0, total - done)
        d['timeline_pct']     = min(round(done / total * 100) if total else 0, 100)
    except Exception:
        d['end_date'] = d['end_date_display'] = '—'
        d['completed_months'] = d['remaining_months'] = d['timeline_pct'] = 0

    # Nearly paid threshold
    d['nearly_paid'] = d['paid_pct'] >= 90

    return d


# ── Dashboard stats ───────────────────────────────────────────────────────────

def loan_stats(db, uid: int) -> dict:
    active = db.execute(
        "SELECT * FROM loans WHERE user_id=? AND status='active'", (uid,)
    ).fetchall()

    total_active      = len(active)
    total_monthly_emi = sum(r['monthly_emi'] for r in active)
    total_remaining   = sum(get_current_balance(db, r['id'], r['loan_amount']) for r in active)

    today = date_cls.today()
    monthly_income = db.execute(
        "SELECT COALESCE(SUM(amount),0) as s FROM txn "
        "WHERE user_id=? AND type='income' AND strftime('%m',date)=? AND strftime('%Y',date)=?",
        (uid, f'{today.month:02d}', str(today.year))
    ).fetchone()['s']

    debt_to_income = round(total_monthly_emi / monthly_income * 100, 1) if monthly_income else 0

    # DTI classification per spec: <20 healthy, 20-35 moderate, >35 high
    if debt_to_income < 20:
        dti_level = 'healthy'
        dti_color = 'emerald'
    elif debt_to_income <= 35:
        dti_level = 'moderate'
        dti_color = 'amber'
    else:
        dti_level = 'high'
        dti_color = 'rose'

    # Loans ending within 3 months
    cutoff = (today + relativedelta(months=3)).isoformat()
    ending_soon = []
    for r in active:
        try:
            end = (datetime.strptime(r['start_date'], '%Y-%m-%d').date()
                   + relativedelta(months=r['tenure_months']))
            months_left = (end.year - today.year) * 12 + (end.month - today.month)
            if 0 <= months_left <= 3:
                ending_soon.append({
                    'name': r['loan_name'],
                    'loan_type': r['loan_type'],
                    'months_left': months_left,
                })
        except Exception:
            pass

    return {
        'total_active':      total_active,
        'total_monthly_emi': total_monthly_emi,
        'total_remaining':   total_remaining,
        'monthly_income':    monthly_income,
        'debt_to_income':    debt_to_income,
        'dti_level':         dti_level,
        'dti_color':         dti_color,
        'ending_soon':       ending_soon,
    }


# ── Insights ──────────────────────────────────────────────────────────────────

def generate_insights(db, uid: int, stats: dict, loans: list) -> list:
    """Generate actionable loan insights for the dashboard."""
    insights = []

    # High DTI
    if stats['debt_to_income'] > 35:
        insights.append({
            'type': 'warning',
            'icon': 'warning',
            'color': 'rose',
            'text': f"Your debt-to-income ratio is {stats['debt_to_income']}% — above the healthy limit of 35%. Consider paying down high-interest loans first.",
        })
    elif stats['debt_to_income'] > 20:
        insights.append({
            'type': 'caution',
            'icon': 'info',
            'color': 'amber',
            'text': f"Debt-to-income is {stats['debt_to_income']}% — moderate. Keep EMIs manageable before taking new loans.",
        })

    # Loans ending soon
    for loan in stats.get('ending_soon', []):
        m = loan['months_left']
        suffix = 'this month' if m == 0 else (f'in {m} month' + ('s' if m > 1 else ''))
        insights.append({
            'type': 'info',
            'icon': 'celebration',
            'color': 'emerald',
            'text': f"Your {loan['name']} ({loan['loan_type']}) will be fully paid {suffix}. 🎉",
        })

    # Nearly paid loans
    for loan in loans:
        if loan.get('nearly_paid') and loan.get('status') == 'active':
            insights.append({
                'type': 'positive',
                'icon': 'trending_up',
                'color': 'emerald',
                'text': f"{loan['loan_name']} is {loan['paid_pct']}% paid — you're almost there!",
            })

    # No income recorded
    if stats['monthly_income'] == 0 and stats['total_active'] > 0:
        insights.append({
            'type': 'info',
            'icon': 'info',
            'color': 'slate',
            'text': 'Add your income transactions this month to calculate your debt-to-income ratio accurately.',
        })

    return insights


# ── Extra payment ─────────────────────────────────────────────────────────────

def apply_extra_payment(db, loan_id: int, uid: int, extra_amount: float) -> dict:
    """
    Apply an extra principal payment to a loan.
    Returns a dict with savings info.
    """
    loan = db.execute(
        'SELECT * FROM loans WHERE id=? AND user_id=?', (loan_id, uid)
    ).fetchone()
    if not loan:
        return {'error': 'Loan not found'}

    today = date_cls.today()
    current_balance = get_current_balance(db, loan_id, loan['loan_amount'])
    new_balance = max(0.0, round(current_balance - extra_amount, 2))

    # Calculate original interest remaining
    next_month_num = get_next_unpaid_month(db, loan_id) or (loan['tenure_months'] + 1)
    original_remaining = db.execute(
        'SELECT SUM(interest_component) as s FROM loan_schedule '
        'WHERE loan_id=? AND month_number>=?', (loan_id, next_month_num)
    ).fetchone()['s'] or 0
    original_months_remaining = db.execute(
        'SELECT COUNT(*) as c FROM loan_schedule WHERE loan_id=? AND month_number>=?',
        (loan_id, next_month_num)
    ).fetchone()['c']

    if new_balance <= 0:
        # Loan fully paid
        db.execute('DELETE FROM loan_schedule WHERE loan_id=? AND month_number>=?',
                   (loan_id, next_month_num))
        db.execute("UPDATE loans SET status='closed' WHERE id=? AND user_id=?",
                   (loan_id, uid))
        db.execute("UPDATE obligations SET status='closed' WHERE source_type='loan' AND source_id=? AND user_id=?",
                   (loan_id, uid))
        interest_saved = round(original_remaining, 2)
        months_saved = original_months_remaining
    else:
        try:
            start_date = datetime.strptime(loan['start_date'], '%Y-%m-%d').date()
        except Exception:
            start_date = today

        _, new_interest_remaining, new_months = rebuild_schedule_from(
            db, loan_id, next_month_num, new_balance,
            loan['interest_rate'], loan['monthly_emi'], start_date
        )
        interest_saved = round(original_remaining - new_interest_remaining, 2)
        months_saved   = original_months_remaining - new_months

    db.commit()
    return {
        'new_balance':     new_balance,
        'interest_saved':  max(0, interest_saved),
        'months_saved':    max(0, months_saved),
        'fully_paid':      new_balance <= 0,
    }
