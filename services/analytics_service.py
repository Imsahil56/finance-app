"""
analytics_service.py
Core analytical calculations for the Analysis module.
All business logic lives here; routes stay thin.
"""

from datetime import datetime, date, timedelta
from collections import defaultdict, OrderedDict
import statistics
import math


# ─────────────────────────────────────────────
# Date-range helpers
# ─────────────────────────────────────────────

def parse_date_range(period: str, custom_start=None, custom_end=None):
    """
    Return (start_date, end_date) for a named period.
    Supported: 'last_30', 'last_month', 'last_quarter', 'custom'
    """
    today = date.today()

    if period == 'last_30':
        return today - timedelta(days=30), today

    if period == 'last_month':
        first_this = today.replace(day=1)
        last_month_end = first_this - timedelta(days=1)
        last_month_start = last_month_end.replace(day=1)
        return last_month_start, last_month_end

    if period == 'last_quarter':
        # Previous 3 full months
        first_this = today.replace(day=1)
        end = first_this - timedelta(days=1)
        start = (end.replace(day=1) - timedelta(days=60)).replace(day=1)
        return start, end

    if period == 'custom' and custom_start and custom_end:
        try:
            s = datetime.strptime(custom_start, '%Y-%m-%d').date()
            e = datetime.strptime(custom_end, '%Y-%m-%d').date()
            return s, e
        except ValueError:
            pass

    # Default: current month
    return today.replace(day=1), today


def prev_period_dates(start: date, end: date):
    """Return the equivalent previous period dates."""
    span = (end - start).days + 1
    prev_end = start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=span - 1)
    return prev_start, prev_end


# ─────────────────────────────────────────────
# Raw query helpers (accept db connection)
# ─────────────────────────────────────────────

def fetch_transactions(db, uid: int, start: date, end: date):
    """All transactions for user in date range."""
    return db.execute(
        "SELECT * FROM txn WHERE user_id=? AND date BETWEEN ? AND ? ORDER BY date ASC",
        (uid, start.isoformat(), end.isoformat())
    ).fetchall()


def fetch_all_transactions(db, uid: int):
    """All-time transactions."""
    return db.execute(
        "SELECT * FROM txn WHERE user_id=? ORDER BY date ASC", (uid,)
    ).fetchall()


def fetch_category_budgets(db, uid: int, month: int = None, year: int = None):
    """
    Fetch category budgets for a specific month.
    Falls back to default_category_budget if no monthly rows exist.
    """
    from datetime import date
    if month is None or year is None:
        today = date.today()
        month, year = today.month, today.year

    rows = db.execute(
        "SELECT id, user_id, category, amount as budget_amount "
        "FROM monthly_category_budget WHERE user_id=? AND month=? AND year=?",
        (uid, month, year)
    ).fetchall()

    if not rows:
        # Fall back to defaults
        rows = db.execute(
            "SELECT id, user_id, category, amount as budget_amount "
            "FROM default_category_budget WHERE user_id=?",
            (uid,)
        ).fetchall()

    return rows


def fetch_monthly_budget(db, uid: int, month: int, year: int):
    return db.execute(
        "SELECT budget_amount FROM monthly_budget WHERE user_id=? AND month=? AND year=?",
        (uid, month, year)
    ).fetchone()


# ─────────────────────────────────────────────
# SPENDING DEEP DIVE
# ─────────────────────────────────────────────

def get_spending_overview(db, uid: int, start: date, end: date, compare: bool = False):
    """
    Returns dict for Spending Deep Dive overview tab.
    """
    rows = fetch_transactions(db, uid, start, end)
    expenses = [r for r in rows if r['type'] == 'expense']
    incomes  = [r for r in rows if r['type'] == 'income']

    total_expense = sum(r['amount'] for r in expenses)
    total_income  = sum(r['amount'] for r in incomes)

    # Category breakdown
    cat_totals: dict[str, float] = defaultdict(float)
    for r in expenses:
        cat_totals[r['category']] += r['amount']

    sorted_cats = sorted(cat_totals.items(), key=lambda x: x[1], reverse=True)
    top_category = sorted_cats[0] if sorted_cats else None

    cat_data = []
    for cat, amt in sorted_cats:
        pct = round(amt / total_expense * 100, 1) if total_expense else 0
        cat_data.append({'category': cat, 'amount': amt, 'percent': pct})

    # Essential vs lifestyle split
    ESSENTIAL = {'Rent & Housing', 'Utilities', 'Health & Medical', 'Education', 'Insurance'}
    essential = sum(r['amount'] for r in expenses if r['category'] in ESSENTIAL)
    lifestyle = total_expense - essential
    essential_pct = round(essential / total_expense * 100) if total_expense else 0

    # vs previous period
    comparison = None
    if compare:
        p_start, p_end = prev_period_dates(start, end)
        p_rows = fetch_transactions(db, uid, p_start, p_end)
        p_expense = sum(r['amount'] for r in p_rows if r['type'] == 'expense')
        if p_expense:
            change_pct = round((total_expense - p_expense) / p_expense * 100, 1)
        else:
            change_pct = None
        comparison = {'prev_expense': p_expense, 'change_pct': change_pct}

    return {
        'total_expense': total_expense,
        'total_income': total_income,
        'net': total_income - total_expense,
        'category_data': cat_data,
        'top_category': top_category,
        'essential': essential,
        'lifestyle': lifestyle,
        'essential_pct': essential_pct,
        'comparison': comparison,
    }


def get_spending_categories(db, uid: int, start: date, end: date):
    """
    Detailed per-category analysis with % change vs previous period.
    """
    rows = fetch_transactions(db, uid, start, end)
    expenses = [r for r in rows if r['type'] == 'expense']
    total_expense = sum(r['amount'] for r in expenses)

    # Previous period
    p_start, p_end = prev_period_dates(start, end)
    p_rows = fetch_transactions(db, uid, p_start, p_end)
    p_expenses = [r for r in p_rows if r['type'] == 'expense']

    cur_totals: dict[str, float] = defaultdict(float)
    for r in expenses:
        cur_totals[r['category']] += r['amount']

    prev_totals: dict[str, float] = defaultdict(float)
    for r in p_expenses:
        prev_totals[r['category']] += r['amount']

    result = []
    for cat, amt in sorted(cur_totals.items(), key=lambda x: x[1], reverse=True):
        prev = prev_totals.get(cat, 0)
        if prev:
            pct_change = round((amt - prev) / prev * 100, 1)
        else:
            pct_change = None  # new this period

        direction = 'up' if (pct_change or 0) > 0 else ('down' if (pct_change or 0) < 0 else 'flat')
        result.append({
            'category': cat,
            'amount': amt,
            'prev_amount': prev,
            'percent_of_total': round(amt / total_expense * 100, 1) if total_expense else 0,
            'pct_change': pct_change,
            'direction': direction,
        })

    return result


def get_spending_trends(db, uid: int, months: int = 6):
    """
    Monthly expense totals + 3-month moving average for the last N months.
    Returns list of {label, month_key, total, moving_avg}
    """
    today = date.today()
    monthly: OrderedDict = OrderedDict()

    for i in range(months - 1, -1, -1):
        # Walk back month-by-month
        m = (today.month - i - 1) % 12 + 1
        y = today.year + ((today.month - i - 1) // 12)
        key = f"{y}-{m:02d}"
        monthly[key] = {'label': datetime(y, m, 1).strftime('%b').upper(),
                        'month': m, 'year': y, 'total': 0.0}

    # Fetch last N months of data
    start = date(today.year + ((today.month - months) // 12),
                 (today.month - months) % 12 + 1, 1) if months <= today.month \
        else date(today.year - 1, 12 - (months - today.month - 1), 1)

    rows = db.execute(
        "SELECT date, amount FROM txn WHERE user_id=? AND type='expense' AND date>=?",
        (uid, start.isoformat())
    ).fetchall()

    for r in rows:
        key = r['date'][:7]
        if key in monthly:
            monthly[key]['total'] += r['amount']

    # 3-month moving average
    vals = list(monthly.values())
    for i, m in enumerate(vals):
        window = [vals[j]['total'] for j in range(max(0, i - 2), i + 1)]
        m['moving_avg'] = round(sum(window) / len(window), 2)

    return vals


# ─────────────────────────────────────────────
# BUDGET PERFORMANCE
# ─────────────────────────────────────────────

def get_budget_performance(db, uid: int):
    """
    Returns: avg_utilization, overshoot_frequency, discipline_score,
             monthly_discipline_trend (last 6 months), category_breakdown.
    """
    today = date.today()

    # Category budgets
    cat_budgets = {cb['category']: cb['budget_amount']
                   for cb in fetch_category_budgets(db, uid)}

    # Last 6 months of monthly budget utilization
    monthly_utils = []
    for i in range(5, -1, -1):
        m = (today.month - i - 1) % 12 + 1
        y = today.year + ((today.month - i - 1) // 12)
        budget_row = fetch_monthly_budget(db, uid, m, y)
        budget_amt = budget_row['budget_amount'] if budget_row else 0

        expense = db.execute(
            "SELECT COALESCE(SUM(amount),0) as s FROM txn "
            "WHERE user_id=? AND type='expense' "
            "AND strftime('%m',date)=? AND strftime('%Y',date)=?",
            (uid, f'{m:02d}', str(y))
        ).fetchone()['s']

        util = round(expense / budget_amt * 100, 1) if budget_amt else 0
        monthly_utils.append({
            'label': datetime(y, m, 1).strftime('%b'),
            'month': m, 'year': y,
            'budget': budget_amt,
            'spent': expense,
            'utilization': util,
            'overshot': expense > budget_amt and budget_amt > 0,
        })

    # KPIs
    utils_with_budget = [u['utilization'] for u in monthly_utils if u['budget'] > 0]
    avg_utilization = round(statistics.mean(utils_with_budget), 1) if utils_with_budget else 0

    total_months = len([u for u in monthly_utils if u['budget'] > 0])
    overshoot_count = sum(1 for u in monthly_utils if u['overshot'])
    overshoot_freq = round(overshoot_count / total_months * 100) if total_months else 0

    # Discipline score: starts at 100, penalise for overshoots + high utilisation
    score = 100
    for u in monthly_utils:
        if u['overshot']:
            score -= 8
        elif u['utilization'] > 90:
            score -= 3
        elif u['utilization'] > 80:
            score -= 1
    discipline_score = max(0, min(100, score))

    # Category breakdown (current month)
    cm, cy = today.month, today.year
    cat_breakdown = []
    for cat, budget_amt in cat_budgets.items():
        spent = db.execute(
            "SELECT COALESCE(SUM(amount),0) as s FROM txn "
            "WHERE user_id=? AND category=? AND type='expense' "
            "AND strftime('%m',date)=? AND strftime('%Y',date)=?",
            (uid, cat, f'{cm:02d}', str(cy))
        ).fetchone()['s']
        util = round(spent / budget_amt * 100, 1) if budget_amt else 0
        if util >= 100:
            status = 'overshot'
        elif util >= 80:
            status = 'warning'
        else:
            status = 'on_track'
        cat_breakdown.append({
            'category': cat,
            'allocated': budget_amt,
            'spent': spent,
            'utilization': util,
            'status': status,
        })

    cat_breakdown.sort(key=lambda x: x['utilization'], reverse=True)

    # prev month comparison for KPIs
    pm = (today.month - 2) % 12 + 1
    py = today.year + ((today.month - 2) // 12)
    prev_util = next((u['utilization'] for u in monthly_utils
                      if u['month'] == pm and u['year'] == py), None)
    avg_util_change = round(avg_utilization - prev_util, 1) if prev_util is not None else None

    return {
        'avg_utilization': avg_utilization,
        'avg_util_change': avg_util_change,
        'overshoot_frequency': overshoot_freq,
        'overshoot_count': overshoot_count,
        'discipline_score': discipline_score,
        'monthly_trend': monthly_utils,
        'category_breakdown': cat_breakdown,
    }


# ─────────────────────────────────────────────
# TRENDS & COMPARISON
# ─────────────────────────────────────────────

def get_trends_data(db, uid: int, granularity: str = 'monthly', compare: bool = False):
    """
    Income vs Expense bar chart + cumulative savings trend.
    granularity: 'monthly' | 'quarterly' | 'yearly'
    """
    today = date.today()
    all_rows = fetch_all_transactions(db, uid)

    # Group by month
    monthly_map: OrderedDict = OrderedDict()
    for r in all_rows:
        key = r['date'][:7]
        if key not in monthly_map:
            dt = datetime.strptime(key, '%Y-%m')
            monthly_map[key] = {'label': dt.strftime('%b %y'), 'income': 0.0, 'expense': 0.0}
        if r['type'] == 'income':
            monthly_map[key]['income'] += r['amount']
        else:
            monthly_map[key]['expense'] += r['amount']

    months = list(monthly_map.values())

    # Rolling 3-month moving average on net savings
    net_vals = []
    running_savings = 0.0
    for m in months:
        running_savings += m['income'] - m['expense']
        net_vals.append(running_savings)

    for i, m in enumerate(months):
        window = net_vals[max(0, i-2):i+1]
        m['net_cumulative'] = round(net_vals[i], 2)
        m['moving_avg'] = round(sum(window) / len(window), 2)

    total_savings = net_vals[-1] if net_vals else 0

    # Quarterly / yearly aggregation
    if granularity == 'quarterly':
        months = _aggregate_quarterly(months)
    elif granularity == 'yearly':
        months = _aggregate_yearly(months)

    # Comparison overlay (previous equivalent period)
    comparison_months = None
    if compare and len(months) >= 2:
        half = len(months) // 2
        comparison_months = months[:half]
        months = months[half:]

    return {
        'monthly_data': months,
        'comparison_data': comparison_months,
        'total_savings': round(total_savings, 2),
    }


def _aggregate_quarterly(months):
    quarters = OrderedDict()
    for m in months:
        # parse label back — safer to reprocess
        pass  # kept simple: just return months for now
    return months


def _aggregate_yearly(months):
    return months


# ─────────────────────────────────────────────
# ANALYSIS OVERVIEW (summary of all 4 cards)
# ─────────────────────────────────────────────

def get_analysis_overview(db, uid: int, start: date, end: date, compare: bool = False):
    """Aggregate mini-preview data for all 4 analysis cards."""

    spending = get_spending_overview(db, uid, start, end, compare)

    # Budget Performance preview
    today = date.today()
    monthly_budget_row = fetch_monthly_budget(db, uid, today.month, today.year)
    budget_amt = monthly_budget_row['budget_amount'] if monthly_budget_row else 0
    current_expense = sum(
        r['amount'] for r in fetch_transactions(db, uid,
            today.replace(day=1), today)
        if r['type'] == 'expense'
    )
    budget_util = round(current_expense / budget_amt * 100) if budget_amt else 0

    cat_budgets = fetch_category_budgets(db, uid)
    overshoot_cats = 0
    for cb in cat_budgets:
        spent = db.execute(
            "SELECT COALESCE(SUM(amount),0) as s FROM txn "
            "WHERE user_id=? AND category=? AND type='expense' "
            "AND strftime('%m',date)=? AND strftime('%Y',date)=?",
            (uid, cb['category'], f'{today.month:02d}', str(today.year))
        ).fetchone()['s']
        if spent > cb['budget_amount']:
            overshoot_cats += 1

    # Trends preview (savings growth this vs last month)
    lm = (today.month - 2) % 12 + 1
    ly = today.year + ((today.month - 2) // 12)
    last_m_rows = fetch_transactions(db, uid,
        date(ly, lm, 1), date(ly, lm, 28))
    last_m_net = sum(r['amount'] * (1 if r['type'] == 'income' else -1)
                     for r in last_m_rows)
    this_m_rows = fetch_transactions(db, uid, today.replace(day=1), today)
    this_m_net = sum(r['amount'] * (1 if r['type'] == 'income' else -1)
                     for r in this_m_rows)

    savings_growth = None
    if last_m_net and last_m_net != 0:
        savings_growth = round((this_m_net - last_m_net) / abs(last_m_net) * 100, 1)

    # Weekly trend mini chart (Mon-Sun)
    week_start = today - timedelta(days=today.weekday())
    week_data = []
    for d in range(7):
        day = week_start + timedelta(days=d)
        day_rows = [r for r in fetch_transactions(db, uid, day, day)]
        week_data.append({
            'label': day.strftime('%a')[:2],
            'expense': sum(r['amount'] for r in day_rows if r['type'] == 'expense'),
            'income': sum(r['amount'] for r in day_rows if r['type'] == 'income'),
        })

    return {
        'spending': spending,
        'budget_util': budget_util,
        'overshoot_cats': overshoot_cats,
        'savings_growth': savings_growth,
        'week_data': week_data,
    }
