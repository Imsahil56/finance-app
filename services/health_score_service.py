"""
health_score_service.py
Financial Health Score: weighted composite of 5 components.

Weights:
  Savings Rate        → 30 pts
  Budget Adherence    → 25 pts
  Expense Stability   → 20 pts
  Income Consistency  → 15 pts
  Category Risk       → 10 pts
"""

from datetime import date, timedelta
from collections import defaultdict
import statistics
import math


ESSENTIAL_CATS = {'Rent & Housing', 'Utilities', 'Health & Medical', 'Education', 'Insurance'}
DISCRETIONARY_CATS = {'Entertainment', 'Travel', 'Personal Care', 'Shopping', 'Subscriptions', 'Other'}


def compute_health_score(db, uid: int):
    today = date.today()

    # ── Fetch 3-month window ──
    three_months_ago = (today.replace(day=1) -
                        timedelta(days=1)).replace(day=1)
    three_months_ago = three_months_ago.replace(
        month=((three_months_ago.month - 3) % 12) or 12,
        year=three_months_ago.year if three_months_ago.month > 3
             else three_months_ago.year - 1
    )

    rows = db.execute(
        "SELECT type, amount, category, date FROM txn "
        "WHERE user_id=? AND date>=? ORDER BY date ASC",
        (uid, three_months_ago.isoformat())
    ).fetchall()

    incomes  = [r for r in rows if r['type'] == 'income']
    expenses = [r for r in rows if r['type'] == 'expense']

    total_income  = sum(r['amount'] for r in incomes)
    total_expense = sum(r['amount'] for r in expenses)

    # ── Component 1: Savings Rate (max 30 pts) ──
    savings_rate = 0.0
    if total_income > 0:
        savings_rate = max(0, (total_income - total_expense) / total_income)
    sr_score = _score_savings_rate(savings_rate)

    # ── Component 2: Budget Adherence (max 25 pts) ──
    ba_score, adherence_pct = _score_budget_adherence(db, uid, today)

    # ── Component 3: Expense Stability (max 20 pts) ──
    es_score, expense_variance = _score_expense_stability(db, uid, today)

    # ── Component 4: Income Consistency (max 15 pts) ──
    ic_score, income_label = _score_income_consistency(db, uid, today)

    # ── Component 5: Category Risk (max 10 pts) ──
    cr_score, risk_data = _score_category_risk(expenses, total_expense)

    total_score = round(sr_score + ba_score + es_score + ic_score + cr_score)
    total_score = max(0, min(100, total_score))

    # ── Status label ──
    if total_score >= 80:
        status = 'Excellent'
        status_color = 'emerald'
    elif total_score >= 60:
        status = 'Good'
        status_color = 'primary'
    elif total_score >= 40:
        status = 'Fair'
        status_color = 'amber'
    else:
        status = 'Poor'
        status_color = 'rose'

    # ── Previous month comparison ──
    prev_score = _prev_month_score(db, uid, today)
    improvement = round(total_score - prev_score, 1) if prev_score is not None else None

    # ── Circle chart: stroke-dashoffset ──
    # SVG circle r=88 → circumference ≈ 552.92
    circumference = 2 * math.pi * 88
    dash_offset = round(circumference * (1 - total_score / 100), 2)

    return {
        'score': total_score,
        'status': status,
        'status_color': status_color,
        'improvement': improvement,
        'circumference': round(circumference, 2),
        'dash_offset': dash_offset,
        'components': {
            'savings_rate': {
                'label': 'Savings Rate',
                'icon': 'savings',
                'value': f"{round(savings_rate * 100)}%",
                'score': round(sr_score),
                'max': 30,
                'status': _savings_status(savings_rate),
                'status_color': _savings_color(savings_rate),
            },
            'budget_adherence': {
                'label': 'Budget Adherence',
                'icon': 'fact_check',
                'value': f"{adherence_pct}%",
                'score': round(ba_score),
                'max': 25,
                'status': 'Good' if adherence_pct >= 80 else 'Needs Work',
                'status_color': 'primary' if adherence_pct >= 80 else 'amber',
            },
            'expense_stability': {
                'label': 'Expense Stability',
                'icon': 'query_stats',
                'value': f"{expense_variance}% Var",
                'score': round(es_score),
                'max': 20,
                'status': 'Stable' if expense_variance < 20 else 'Volatile',
                'status_color': 'slate' if expense_variance < 20 else 'amber',
            },
            'income_consistency': {
                'label': 'Income Consistency',
                'icon': 'payments',
                'value': income_label,
                'score': round(ic_score),
                'max': 15,
                'status': 'Predictable' if ic_score >= 12 else 'Variable',
                'status_color': 'emerald' if ic_score >= 12 else 'amber',
            },
        },
        'risk_profile': risk_data,
        'total_income': total_income,
        'total_expense': total_expense,
        'savings_rate_pct': round(savings_rate * 100),
        'adherence_pct': adherence_pct,
        'expense_variance': expense_variance,
        'income_label': income_label,
    }


# ─────────────────────────────────────────────
# Component scorers
# ─────────────────────────────────────────────

def _score_savings_rate(rate: float) -> float:
    """30 points max. Excellent: 20%+, Good: 10-19%, Fair: 1-9%, Poor: ≤0"""
    if rate >= 0.20:
        return 30
    if rate >= 0.10:
        return 22 + (rate - 0.10) / 0.10 * 8
    if rate >= 0.05:
        return 12 + (rate - 0.05) / 0.05 * 10
    if rate > 0:
        return 5
    return 0


def _savings_status(rate: float) -> str:
    if rate >= 0.20: return 'Excellent'
    if rate >= 0.10: return 'Good'
    if rate > 0:    return 'Fair'
    return 'Poor'


def _savings_color(rate: float) -> str:
    if rate >= 0.20: return 'emerald'
    if rate >= 0.10: return 'primary'
    if rate > 0:    return 'amber'
    return 'rose'


def _score_budget_adherence(db, uid: int, today: date):
    """25 points max. % of months where expense ≤ budget."""
    months_checked = 0
    months_ok = 0
    for i in range(3):
        m = (today.month - i - 1) % 12 + 1
        y = today.year + ((today.month - i - 1) // 12)
        budget_row = db.execute(
            "SELECT budget_amount FROM monthly_budget WHERE user_id=? AND month=? AND year=?",
            (uid, m, y)
        ).fetchone()
        if not budget_row:
            continue
        spent = db.execute(
            "SELECT COALESCE(SUM(amount),0) as s FROM txn "
            "WHERE user_id=? AND type='expense' "
            "AND strftime('%m',date)=? AND strftime('%Y',date)=?",
            (uid, f'{m:02d}', str(y))
        ).fetchone()['s']
        months_checked += 1
        if spent <= budget_row['budget_amount']:
            months_ok += 1

    if months_checked == 0:
        return 20, 80  # no budget set: neutral score

    adherence_pct = round(months_ok / months_checked * 100)
    score = adherence_pct / 100 * 25
    return score, adherence_pct


def _score_expense_stability(db, uid: int, today: date):
    """20 points max. Coefficient of variation of monthly expenses."""
    monthly_totals = []
    for i in range(6):
        m = (today.month - i - 1) % 12 + 1
        y = today.year + ((today.month - i - 1) // 12)
        val = db.execute(
            "SELECT COALESCE(SUM(amount),0) as s FROM txn "
            "WHERE user_id=? AND type='expense' "
            "AND strftime('%m',date)=? AND strftime('%Y',date)=?",
            (uid, f'{m:02d}', str(y))
        ).fetchone()['s']
        if val > 0:
            monthly_totals.append(val)

    if len(monthly_totals) < 2:
        return 15, 5  # insufficient data: neutral

    mean_val = statistics.mean(monthly_totals)
    stdev_val = statistics.stdev(monthly_totals)
    cv = round(stdev_val / mean_val * 100, 1) if mean_val else 0

    # Lower CV = more stable = higher score
    if cv < 10:
        score = 20
    elif cv < 20:
        score = 16
    elif cv < 35:
        score = 10
    elif cv < 50:
        score = 5
    else:
        score = 2

    return score, cv


def _score_income_consistency(db, uid: int, today: date):
    """15 points max. Consistency of monthly income."""
    monthly_income = []
    for i in range(6):
        m = (today.month - i - 1) % 12 + 1
        y = today.year + ((today.month - i - 1) // 12)
        val = db.execute(
            "SELECT COALESCE(SUM(amount),0) as s FROM txn "
            "WHERE user_id=? AND type='income' "
            "AND strftime('%m',date)=? AND strftime('%Y',date)=?",
            (uid, f'{m:02d}', str(y))
        ).fetchone()['s']
        monthly_income.append(val)

    nonzero = [v for v in monthly_income if v > 0]
    if len(nonzero) < 2:
        label = 'Low'
        return 5, label

    cv = statistics.stdev(nonzero) / statistics.mean(nonzero) if statistics.mean(nonzero) else 1
    months_with_income = len(nonzero)

    if months_with_income >= 5 and cv < 0.15:
        label, score = 'High', 15
    elif months_with_income >= 4 and cv < 0.30:
        label, score = 'Medium', 10
    elif months_with_income >= 3:
        label, score = 'Variable', 6
    else:
        label, score = 'Low', 3

    return score, label


def _score_category_risk(expenses, total_expense: float):
    """
    10 points max. Penalise high discretionary spending.
    Returns (score, risk_profile_data).
    """
    essential_total = sum(r['amount'] for r in expenses
                          if r['category'] in ESSENTIAL_CATS)
    discretionary_total = sum(r['amount'] for r in expenses
                               if r['category'] in DISCRETIONARY_CATS)
    flexible_total = total_expense - essential_total - discretionary_total

    def pct(v):
        return round(v / total_expense * 100) if total_expense else 0

    essential_pct = pct(essential_total)
    discretionary_pct = pct(discretionary_total)
    flexible_pct = pct(flexible_total)

    # Score: discretionary >50% = risky
    if discretionary_pct < 20:
        score = 10
    elif discretionary_pct < 35:
        score = 7
    elif discretionary_pct < 50:
        score = 4
    else:
        score = 1

    # Risk level per segment
    if essential_pct < 40:
        ess_risk, ess_color, ess_bar_color = 'Low Risk', 'emerald', 'emerald'
    elif essential_pct < 60:
        ess_risk, ess_color, ess_bar_color = 'Moderate', 'primary', 'primary'
    else:
        ess_risk, ess_color, ess_bar_color = 'High', 'amber', 'amber'

    flex_risk = 'Moderate' if 20 <= flexible_pct <= 40 else ('Low Risk' if flexible_pct < 20 else 'High')
    disc_risk = 'Controlled' if discretionary_pct < 25 else ('Moderate' if discretionary_pct < 40 else 'High')

    risk_data = [
        {
            'label': 'Essential Expenses',
            'description': 'Housing, utilities, and core necessities remain within sustainable historical thresholds.',
            'percent': essential_pct,
            'risk': ess_risk,
            'risk_color': ess_color,
            'bar_color': ess_bar_color,
        },
        {
            'label': 'Flexible Expenses',
            'description': 'Subscription and lifestyle services show seasonal fluctuations but track with budget targets.',
            'percent': flexible_pct,
            'risk': flex_risk,
            'risk_color': 'primary',
            'bar_color': 'primary',
        },
        {
            'label': 'Discretionary Spending',
            'description': 'Non-essential spending is currently optimised for long-term capital preservation goals.',
            'percent': discretionary_pct,
            'risk': disc_risk,
            'risk_color': 'slate',
            'bar_color': 'slate',
        },
    ]

    return score, risk_data


def _prev_month_score(db, uid: int, today: date):
    """Compute last month's approximate score for comparison."""
    m = (today.month - 2) % 12 + 1
    y = today.year + ((today.month - 2) // 12)
    income = db.execute(
        "SELECT COALESCE(SUM(amount),0) as s FROM txn "
        "WHERE user_id=? AND type='income' "
        "AND strftime('%m',date)=? AND strftime('%Y',date)=?",
        (uid, f'{m:02d}', str(y))
    ).fetchone()['s']
    expense = db.execute(
        "SELECT COALESCE(SUM(amount),0) as s FROM txn "
        "WHERE user_id=? AND type='expense' "
        "AND strftime('%m',date)=? AND strftime('%Y',date)=?",
        (uid, f'{m:02d}', str(y))
    ).fetchone()['s']

    if income == 0 and expense == 0:
        return None

    rate = max(0, (income - expense) / income) if income else 0
    return _score_savings_rate(rate) + 20 + 15 + 10 + 8  # rough approximation
