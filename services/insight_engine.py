"""
insight_engine.py
Rule-based financial insight generator.
Returns a list of insight dicts for display on the Analysis Overview.
"""

from datetime import date, timedelta
from collections import defaultdict


INSIGHT_RULES = []


def generate_insights(db, uid: int, spending: dict, budget_perf: dict,
                      health: dict) -> list[dict]:
    """
    Returns list of insight dicts: {type, icon, title, body, color}
    """
    insights = []
    today = date.today()

    # ── Rule 1: Category spike (>15% increase) ──
    for cat in spending.get('category_data', []):
        # compare this month vs last
        m = (today.month - 2) % 12 + 1
        y = today.year + ((today.month - 2) // 12)
        prev = db.execute(
            "SELECT COALESCE(SUM(amount),0) as s FROM txn "
            "WHERE user_id=? AND category=? AND type='expense' "
            "AND strftime('%m',date)=? AND strftime('%Y',date)=?",
            (uid, cat['category'], f'{m:02d}', str(y))
        ).fetchone()['s']
        if prev > 0 and cat['amount'] > 0:
            change = (cat['amount'] - prev) / prev * 100
            if change > 15:
                insights.append({
                    'type': 'warning',
                    'icon': 'trending_up',
                    'title': f"{cat['category']} spending up {round(change)}%",
                    'body': f"You spent ₹{cat['amount']:,.0f} vs ₹{prev:,.0f} last period. Consider reviewing this category.",
                    'color': 'amber',
                })

    # ── Rule 2: Budget overshoot ──
    if budget_perf.get('overshoot_count', 0) > 0:
        insights.append({
            'type': 'alert',
            'icon': 'account_balance_wallet',
            'title': f"Budget exceeded in {budget_perf['overshoot_count']} categor{'y' if budget_perf['overshoot_count'] == 1 else 'ies'}",
            'body': "You have overshot your budget this month. Review your spending limits.",
            'color': 'rose',
        })

    # ── Rule 3: Savings improvement ──
    if health.get('improvement') and health['improvement'] > 0:
        insights.append({
            'type': 'positive',
            'icon': 'savings',
            'title': f"Health score improved by {health['improvement']} pts",
            'body': "Great job! Your financial discipline has improved compared to last month.",
            'color': 'emerald',
        })

    # ── Rule 4: High expense volatility ──
    variance = health.get('expense_variance', 0)
    if variance > 35:
        insights.append({
            'type': 'warning',
            'icon': 'query_stats',
            'title': 'High expense volatility detected',
            'body': f"Your monthly spending varies by {variance}%. Try to maintain more consistent spending habits.",
            'color': 'amber',
        })

    # ── Rule 5: Income drop ──
    m = (today.month - 2) % 12 + 1
    y = today.year + ((today.month - 2) // 12)
    prev_income = db.execute(
        "SELECT COALESCE(SUM(amount),0) as s FROM txn "
        "WHERE user_id=? AND type='income' "
        "AND strftime('%m',date)=? AND strftime('%Y',date)=?",
        (uid, f'{m:02d}', str(y))
    ).fetchone()['s']
    curr_income = spending.get('total_income', 0)
    if prev_income > 0 and curr_income < prev_income * 0.85:
        drop = round((prev_income - curr_income) / prev_income * 100)
        insights.append({
            'type': 'alert',
            'icon': 'arrow_downward',
            'title': f"Income dropped by {drop}% vs last period",
            'body': "Your income this period is significantly lower. Monitor your cash flow.",
            'color': 'rose',
        })

    # ── Rule 6: Positive savings rate ──
    if health.get('savings_rate_pct', 0) >= 20:
        insights.append({
            'type': 'positive',
            'icon': 'verified',
            'title': f"Excellent savings rate of {health['savings_rate_pct']}%",
            'body': "You're saving over 20% of your income — that puts you in the top tier of savers!",
            'color': 'emerald',
        })

    # ── Rule 7: No budget set ──
    if budget_perf.get('avg_utilization', 0) == 0 and not budget_perf.get('category_breakdown'):
        insights.append({
            'type': 'info',
            'icon': 'lightbulb',
            'title': 'Set a budget to unlock insights',
            'body': "Add monthly budgets to get personalised performance tracking and alerts.",
            'color': 'primary',
        })

    # Max 4 insights to keep UI clean, prioritise warnings first
    order = {'alert': 0, 'warning': 1, 'positive': 2, 'info': 3}
    insights.sort(key=lambda x: order.get(x['type'], 9))
    return insights[:4]
