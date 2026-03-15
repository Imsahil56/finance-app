"""
services/prediction_service.py
Statistical prediction engine for the Financial Intelligence Dashboard.
Uses moving averages, trend extrapolation and variance — no ML.
"""

import calendar
import math
from datetime import date, timedelta
from collections import defaultdict


# ── Helpers ───────────────────────────────────────────────────────────────────

def _days_in_month(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]


def _month_transactions(db, uid: int, month: int, year: int, txn_type: str = None):
    q = "SELECT * FROM txn WHERE user_id=? AND strftime('%m',date)=? AND strftime('%Y',date)=?"
    params = [uid, f'{month:02d}', str(year)]
    if txn_type:
        q += " AND type=?"
        params.append(txn_type)
    return db.execute(q, params).fetchall()


def _last_n_months_income(db, uid: int, n: int = 3) -> float:
    """Average monthly income over the last n months."""
    today = date.today()
    totals = []
    for i in range(1, n + 1):
        m = (today.month - i - 1) % 12 + 1
        y = today.year + ((today.month - i - 1) // 12)
        s = db.execute(
            "SELECT COALESCE(SUM(amount),0) as s FROM txn "
            "WHERE user_id=? AND type='income' AND strftime('%m',date)=? AND strftime('%Y',date)=?",
            (uid, f'{m:02d}', str(y))
        ).fetchone()['s']
        if s > 0:
            totals.append(s)
    return sum(totals) / len(totals) if totals else 0.0


# ── 1. Forecast month-end spend ───────────────────────────────────────────────

def forecast_month_spend(db, uid: int) -> dict:
    """
    Forecast total month-end spending based on daily average spend so far.
    Returns: current_spend, forecast_spend, daily_avg, days_passed, days_total, on_track, budget
    """
    today = date.today()
    month, year = today.month, today.year
    days_passed = today.day
    days_total  = _days_in_month(year, month)

    rows = _month_transactions(db, uid, month, year, 'expense')
    current_spend = sum(r['amount'] for r in rows)

    daily_avg     = current_spend / days_passed if days_passed > 0 else 0
    forecast_spend = round(daily_avg * days_total)

    # Monthly budget
    budget_row = db.execute(
        "SELECT budget_amount FROM monthly_budget WHERE user_id=? AND month=? AND year=?",
        (uid, month, year)
    ).fetchone()
    budget = budget_row['budget_amount'] if budget_row else 0

    on_track = forecast_spend <= budget if budget > 0 else None

    # Spend-per-day mini chart (last 7 days)
    daily_chart = []
    for d in range(6, -1, -1):
        day = today - timedelta(days=d)
        amt = db.execute(
            "SELECT COALESCE(SUM(amount),0) as s FROM txn "
            "WHERE user_id=? AND type='expense' AND date=?",
            (uid, day.isoformat())
        ).fetchone()['s']
        daily_chart.append({'label': day.strftime('%d'), 'amount': round(amt)})

    # Compare with last month's spend
    lm = (month - 2) % 12 + 1
    ly = year + ((month - 2) // 12)
    last_month_spend = db.execute(
        "SELECT COALESCE(SUM(amount),0) as s FROM txn "
        "WHERE user_id=? AND type='expense' AND strftime('%m',date)=? AND strftime('%Y',date)=?",
        (uid, f'{lm:02d}', str(ly))
    ).fetchone()['s']

    change_pct = None
    if last_month_spend > 0:
        change_pct = round((forecast_spend - last_month_spend) / last_month_spend * 100, 1)

    return {
        'current_spend':   round(current_spend),
        'forecast_spend':  forecast_spend,
        'daily_avg':       round(daily_avg),
        'days_passed':     days_passed,
        'days_total':      days_total,
        'budget':          round(budget),
        'on_track':        on_track,
        'daily_chart':     daily_chart,
        'change_pct':      change_pct,
    }


# ── 2. Budget risk prediction ─────────────────────────────────────────────────

def predict_budget_risk(db, uid: int) -> list:
    """
    Predict which category budgets may be exceeded by month-end.
    Returns list of risk dicts sorted by risk severity.
    """
    today = date.today()
    month, year = today.month, today.year
    days_passed = max(today.day, 1)
    days_total  = _days_in_month(year, month)
    days_remaining = days_total - days_passed

    # Category budgets
    budgets = db.execute(
        "SELECT category, amount FROM monthly_category_budget WHERE user_id=? AND month=? AND year=?",
        (uid, month, year)
    ).fetchall()
    if not budgets:
        budgets = db.execute(
            "SELECT category, amount FROM default_category_budget WHERE user_id=?", (uid,)
        ).fetchall()

    risks = []
    for b in budgets:
        cat, budget_amt = b['category'], b['amount']
        if budget_amt <= 0:
            continue

        spent = db.execute(
            "SELECT COALESCE(SUM(amount),0) as s FROM txn "
            "WHERE user_id=? AND type='expense' AND category=? "
            "AND strftime('%m',date)=? AND strftime('%Y',date)=?",
            (uid, cat, f'{month:02d}', str(year))
        ).fetchone()['s']

        daily_avg = spent / days_passed
        projected = round(daily_avg * days_total)
        usage_pct  = round(spent / budget_amt * 100)
        proj_pct   = round(projected / budget_amt * 100)

        # Only flag if already actually over budget
        if usage_pct >= 100:
            risk = 'high'
            risk_color = 'rose'
        # Flag high only when both usage is high AND projection significantly overshoots
        elif proj_pct > 110 and usage_pct >= 70:
            risk = 'high'
            risk_color = 'rose'
        # Medium: projection near limit and usage already meaningful
        elif proj_pct > 90 and usage_pct >= 60:
            risk = 'medium'
            risk_color = 'amber'
        # Watch: already used most of budget
        elif usage_pct > 80:
            risk = 'watch'
            risk_color = 'yellow'
        else:
            continue  # Safe — skip

        overshoot = max(0, projected - budget_amt)
        risks.append({
            'category':   cat,
            'budget':     round(budget_amt),
            'spent':      round(spent),
            'projected':  projected,
            'usage_pct':  usage_pct,
            'proj_pct':   proj_pct,
            'overshoot':  round(overshoot),
            'risk':       risk,
            'risk_color': risk_color,
        })

    return sorted(risks, key=lambda x: x['proj_pct'], reverse=True)[:3]


# ── 3. Savings prediction ─────────────────────────────────────────────────────

def predict_savings(db, uid: int) -> dict:
    """
    Predict current savings and projected month-end savings.
    """
    today = date.today()
    month, year = today.month, today.year
    days_passed = max(today.day, 1)
    days_total  = _days_in_month(year, month)

    income_rows  = _month_transactions(db, uid, month, year, 'income')
    expense_rows = _month_transactions(db, uid, month, year, 'expense')

    current_income  = sum(r['amount'] for r in income_rows)
    current_expense = sum(r['amount'] for r in expense_rows)
    current_savings = current_income - current_expense

    # Project income using last 3-month average
    avg_income = _last_n_months_income(db, uid, 3)
    if avg_income == 0:
        avg_income = current_income

    # Project expense
    daily_avg_expense = current_expense / days_passed
    projected_expense = daily_avg_expense * days_total

    projected_savings = round(avg_income - projected_expense)

    # Savings goal: 20% of projected income
    savings_goal = round(avg_income * 0.20) if avg_income > 0 else 0
    goal_progress = 0
    if savings_goal > 0:
        goal_progress = min(100, round(max(0, current_savings) / savings_goal * 100))

    return {
        'current_savings':   round(current_savings),
        'projected_savings': projected_savings,
        'current_income':    round(current_income),
        'current_expense':   round(current_expense),
        'avg_income':        round(avg_income),
        'savings_goal':      savings_goal,
        'goal_progress':     goal_progress,
    }


# ── 4. Health score forecast ──────────────────────────────────────────────────

def predict_health_score(db, uid: int, current_score: int) -> dict:
    """
    Predict next month's health score based on current trends.
    """
    today = date.today()
    month, year = today.month, today.year

    # Savings rate this month
    income  = sum(r['amount'] for r in _month_transactions(db, uid, month, year, 'income'))
    expense = sum(r['amount'] for r in _month_transactions(db, uid, month, year, 'expense'))
    savings_rate = max(0, (income - expense) / income) if income > 0 else 0

    # Budget adherence risk
    risks = predict_budget_risk(db, uid)
    high_risks = sum(1 for r in risks if r['risk'] == 'high')

    # Calculate predicted delta
    delta = 0
    if savings_rate < 0.10:
        delta -= 5
    elif savings_rate > 0.25:
        delta += 3

    delta -= high_risks * 3

    # Expense growth vs last month
    forecast = forecast_month_spend(db, uid)
    if forecast['change_pct'] and forecast['change_pct'] > 15:
        delta -= 4

    predicted_score = max(0, min(100, current_score + delta))

    # Factors summary
    liquidity = 'Excellent' if savings_rate > 0.20 else ('Good' if savings_rate > 0.10 else 'Low')
    liquidity_color = 'emerald' if savings_rate > 0.20 else ('primary' if savings_rate > 0.10 else 'rose')

    return {
        'predicted_score': predicted_score,
        'delta':           delta,
        'will_drop':       delta < -2,
        'liquidity':       liquidity,
        'liquidity_color': liquidity_color,
    }


# ── 5. Debt analytics ─────────────────────────────────────────────────────────

def get_debt_analytics(db, uid: int) -> dict:
    """
    Total debt, EMI, DTI ratio, active loans, payoff trend.
    """
    today = date.today()

    active_loans = db.execute(
        "SELECT * FROM loans WHERE user_id=? AND status='active'", (uid,)
    ).fetchall()

    total_emi       = sum(l['monthly_emi'] for l in active_loans)
    total_remaining = 0
    for l in active_loans:
        row = db.execute(
            "SELECT remaining_balance FROM loan_schedule WHERE loan_id=? "
            "AND payment_date<=? ORDER BY month_number DESC LIMIT 1",
            (l['id'], today.isoformat())
        ).fetchone()
        total_remaining += row['remaining_balance'] if row else l['loan_amount']

    monthly_income = db.execute(
        "SELECT COALESCE(SUM(amount),0) as s FROM txn "
        "WHERE user_id=? AND type='income' AND strftime('%m',date)=? AND strftime('%Y',date)=?",
        (uid, f'{today.month:02d}', str(today.year))
    ).fetchone()['s']

    dti = round(total_emi / monthly_income * 100, 1) if monthly_income > 0 else 0
    dti_color = 'emerald' if dti < 20 else ('amber' if dti <= 35 else 'rose')
    dti_label = 'Healthy' if dti < 20 else ('Moderate' if dti <= 35 else 'High Risk')

    # Payoff trend: sample combined remaining balance from loan_schedule
    trend_points = []
    if active_loans:
        earliest = min(l['start_date'] for l in active_loans)
        # Get next 12 months of combined remaining balance
        for i in range(0, 13, 2):
            future = today + timedelta(days=i * 30)
            total_bal = 0
            for l in active_loans:
                row = db.execute(
                    "SELECT remaining_balance FROM loan_schedule WHERE loan_id=? "
                    "AND payment_date<=? ORDER BY month_number DESC LIMIT 1",
                    (l['id'], future.isoformat())
                ).fetchone()
                if row:
                    total_bal += row['remaining_balance']
                else:
                    total_bal += l['loan_amount']
            trend_points.append({
                'label': future.strftime("%b '%y") if i > 0 else future.strftime("%b '%y"),
                'balance': round(total_bal)
            })

    return {
        'total_outstanding': round(total_remaining),
        'total_emi':         round(total_emi),
        'dti':               dti,
        'dti_color':         dti_color,
        'dti_label':         dti_label,
        'active_count':      len(active_loans),
        'monthly_income':    round(monthly_income),
        'payoff_trend':      trend_points,
    }


# ── 6. Upcoming payments ──────────────────────────────────────────────────────

def get_upcoming_payments(db, uid: int) -> list:
    """
    Upcoming obligations due in the next 14 days.
    """
    today = date.today()
    in_14 = today + timedelta(days=14)

    rows = db.execute(
        """SELECT po.due_date, ob.name, ob.amount, ob.source_type, ob.category
           FROM pending_obligations po
           JOIN obligations ob ON ob.id = po.obligation_id
           WHERE ob.user_id=? AND po.status='pending'
             AND po.due_date BETWEEN ? AND ?
           ORDER BY po.due_date ASC""",
        (uid, today.isoformat(), in_14.isoformat())
    ).fetchall()

    payments = []
    for r in rows:
        due = date.fromisoformat(r['due_date'])
        days_away = (due - today).days
        payments.append({
            'name':      r['name'],
            'amount':    round(r['amount']),
            'due_date':  r['due_date'],
            'days_away': days_away,
            'source':    r['source_type'],
            'category':  r['category'] or 'Other',
            'days_label': 'Today' if days_away == 0 else (
                          'Tomorrow' if days_away == 1 else f'In {days_away} days'),
        })

    return payments


# ── 7. Wealth projection ──────────────────────────────────────────────────────

def predict_wealth_projection(db, uid: int) -> dict:
    """
    Simple linear wealth projection based on average monthly savings.
    """
    today = date.today()

    # Average monthly savings over last 6 months
    savings_list = []
    for i in range(1, 7):
        m = (today.month - i - 1) % 12 + 1
        y = today.year + ((today.month - i - 1) // 12)
        inc = db.execute(
            "SELECT COALESCE(SUM(amount),0) as s FROM txn "
            "WHERE user_id=? AND type='income' AND strftime('%m',date)=? AND strftime('%Y',date)=?",
            (uid, f'{m:02d}', str(y))
        ).fetchone()['s']
        exp = db.execute(
            "SELECT COALESCE(SUM(amount),0) as s FROM txn "
            "WHERE user_id=? AND type='expense' AND strftime('%m',date)=? AND strftime('%Y',date)=?",
            (uid, f'{m:02d}', str(y))
        ).fetchone()['s']
        if inc > 0:
            savings_list.append(inc - exp)

    avg_monthly_savings = sum(savings_list) / len(savings_list) if savings_list else 0

    # Project to 2030
    months_to_2030 = max(0, (2030 - today.year) * 12 + (1 - today.month))
    projected = round(avg_monthly_savings * months_to_2030)

    # Format
    if projected >= 10000000:
        label = f'₹{projected/10000000:.1f}Cr'
    elif projected >= 100000:
        label = f'₹{projected/100000:.1f}L'
    else:
        label = f'₹{projected:,.0f}'

    return {
        'projected_2030':     projected,
        'projected_label':    label,
        'avg_monthly_savings': round(avg_monthly_savings),
        'months_to_2030':     months_to_2030,
    }


# ── Master function ───────────────────────────────────────────────────────────

def get_all_predictions(db, uid: int, current_health_score: int = 0) -> dict:
    """Single call that returns all prediction data for the overview page."""
    return {
        'spend_forecast':  forecast_month_spend(db, uid),
        'budget_risks':    predict_budget_risk(db, uid),
        'savings':         predict_savings(db, uid),
        'health_forecast': predict_health_score(db, uid, current_health_score),
        'debt':            get_debt_analytics(db, uid),
        'upcoming':        get_upcoming_payments(db, uid),
        'wealth':          predict_wealth_projection(db, uid),
    }


# ── Financial status banner ───────────────────────────────────────────────────

def get_financial_status(predictions: dict) -> dict:
    """
    Summarise all risks into a single banner object.
    Returns: level (green/yellow/red), issues[], recommended_action, top_issue
    """
    issues = []
    critical_count = 0
    warning_count  = 0

    # Budget risks
    risks = predictions.get('budget_risks', [])
    high_risks   = [r for r in risks if r['risk'] == 'high']
    med_risks    = [r for r in risks if r['risk'] == 'medium']
    actual_over  = [r for r in high_risks if r['usage_pct'] >= 100]
    if actual_over:
        issues.append({
            'severity': 'critical',
            'text': f"Budget exceeded in {len(actual_over)} {'category' if len(actual_over)==1 else 'categories'}",
            'icon': 'account_balance_wallet',
        })
        critical_count += 1
    elif high_risks:
        issues.append({
            'severity': 'warning',
            'text': f"Budget at risk in {len(high_risks)} {'category' if len(high_risks)==1 else 'categories'}",
            'icon': 'account_balance_wallet',
        })
        warning_count += 1
    elif med_risks:
        issues.append({
            'severity': 'warning',
            'text': f"Budget at risk in {len(med_risks)} {'category' if len(med_risks)==1 else 'categories'}",
            'icon': 'account_balance_wallet',
        })
        warning_count += 1

    # Debt ratio
    debt = predictions.get('debt', {})
    dti  = debt.get('dti', 0)
    if dti > 50:
        issues.append({'severity': 'critical', 'text': f'Debt ratio critically high ({dti}%)', 'icon': 'credit_card'})
        critical_count += 1
    elif dti > 35:
        issues.append({'severity': 'warning', 'text': f'Debt ratio above recommended level ({dti}%)', 'icon': 'credit_card'})
        warning_count += 1

    # Savings
    savings = predictions.get('savings', {})
    if savings.get('projected_savings', 0) < 0:
        issues.append({'severity': 'critical', 'text': 'Savings projection negative this month', 'icon': 'savings'})
        critical_count += 1
    elif savings.get('goal_progress', 100) < 30:
        issues.append({'severity': 'warning', 'text': 'Savings below target pace', 'icon': 'savings'})
        warning_count += 1

    # Health score forecast
    hf = predictions.get('health_forecast', {})
    if hf.get('will_drop'):
        issues.append({'severity': 'warning', 'text': f'Health score may drop to {hf.get("predicted_score")}', 'icon': 'monitor_heart'})
        warning_count += 1

    # Determine overall level
    if critical_count >= 2 or (critical_count >= 1 and warning_count >= 1):
        level = 'red'
        level_label = 'High Financial Risk'
        level_color = 'rose'
    elif critical_count == 1 or warning_count >= 2:
        level = 'yellow'
        level_label = 'Moderate Risk'
        level_color = 'amber'
    elif warning_count == 1:
        level = 'yellow'
        level_label = 'Minor Risk Detected'
        level_color = 'amber'
    else:
        level = 'green'
        level_label = 'Finances Healthy'
        level_color = 'emerald'

    # Recommended action based on most critical issue
    action = None
    if critical_count > 0:
        if high_risks:
            action = f'Review and reduce {high_risks[0]["category"]} spending this month.'
        elif dti > 50:
            action = 'Consider paying down high-interest loans to reduce debt burden.'
        elif savings.get('projected_savings', 0) < 0:
            action = 'Cut discretionary spending to prevent a savings deficit.'
    elif warning_count > 0:
        if med_risks:
            action = f'Monitor {med_risks[0]["category"]} budget — projected to hit limit.'
        elif dti > 35:
            action = 'Avoid taking new debt until income increases.'
        else:
            action = 'Review spending categories to stay on track.'

    # Top financial issue (most severe single item)
    top_issue = None
    if dti > 35 and debt.get('total_emi', 0) > 0:
        top_issue = {
            'title':    f'Debt Ratio: {dti}%',
            'subtitle': 'Recommended Maximum: 35%',
            'impact':   f'₹{debt["total_emi"]:,.0f} monthly obligations',
            'severity': 'critical' if dti > 50 else 'warning',
            'icon':     'credit_card',
        }
    elif high_risks:
        r = high_risks[0]
        top_issue = {
            'title':    f'{r["category"]} Budget Exceeded',
            'subtitle': f'{r["usage_pct"]}% of budget used so far',
            'impact':   f'Projected to exceed by ₹{r["overshoot"]:,.0f}',
            'severity': 'critical',
            'icon':     'account_balance_wallet',
        }
    elif savings.get('projected_savings', 0) < 0:
        top_issue = {
            'title':    'Negative Savings Projected',
            'subtitle': 'Spending exceeds income this month',
            'impact':   f'Deficit: ₹{abs(savings["projected_savings"]):,.0f}',
            'severity': 'critical',
            'icon':     'savings',
        }

    return {
        'level':       level,
        'level_label': level_label,
        'level_color': level_color,
        'issues':      issues,
        'action':      action,
        'top_issue':   top_issue,
        'issue_count': len(issues),
    }