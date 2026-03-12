"""
routes/analysis.py
Analysis blueprint — spending deep-dive, budget performance, trends, health score.
"""

from datetime import datetime, date

from flask import Blueprint, render_template, redirect, url_for, request, session

from routes.db import get_db, login_required, get_monthly_spend
from services.analytics_service  import (parse_date_range, get_spending_overview,
                                          get_spending_categories, get_spending_trends,
                                          get_budget_performance, get_trends_data,
                                          get_analysis_overview)
from services.health_score_service import compute_health_score
from services.insight_engine       import generate_insights

analysis_bp = Blueprint('analysis', __name__, url_prefix='/analysis')


@analysis_bp.route('/', methods=['GET'])
@login_required
def overview():
    db  = get_db()
    uid = session['user_id']

    period   = request.args.get('period', 'last_30')
    compare  = request.args.get('compare', '0') == '1'
    c_start  = request.args.get('start')
    c_end    = request.args.get('end')

    start, end = parse_date_range(period, c_start, c_end)

    # Aggregated data for all 4 cards
    overview_data = get_analysis_overview(db, uid, start, end, compare)

    # Health score preview
    health = compute_health_score(db, uid)

    # Insights
    budget_perf = get_budget_performance(db, uid)
    insights = generate_insights(db, uid, overview_data['spending'], budget_perf, health)

    sidebar_expense, sidebar_pct = get_monthly_spend()

    return render_template('analysis/overview.html',
        period=period, compare=compare, start=start, end=end,
        overview=overview_data, health=health,
        budget_perf=budget_perf, insights=insights,
        sidebar_expense=sidebar_expense, sidebar_pct=sidebar_pct,
    )


# ─────────────────────────────────────────────
# 2. Spending Deep Dive  /analysis/spending
# ─────────────────────────────────────────────

@analysis_bp.route('/spending', methods=['GET'])
@login_required
def spending():
    db  = get_db()
    uid = session['user_id']

    # Month navigation
    today = date.today()
    try:
        sel_month = int(request.args.get('month', today.month))
        sel_year  = int(request.args.get('year',  today.year))
        if not (1 <= sel_month <= 12):
            raise ValueError
    except (ValueError, TypeError):
        sel_month, sel_year = today.month, today.year

    from datetime import datetime
    import calendar
    last_day = calendar.monthrange(sel_year, sel_month)[1]
    start = date(sel_year, sel_month, 1)
    end   = date(sel_year, sel_month, last_day)

    active_tab = request.args.get('tab', 'overview')

    # Overview tab data
    spending_data = get_spending_overview(db, uid, start, end, compare=True)

    # Categories tab data
    categories = get_spending_categories(db, uid, start, end)

    # Trends tab data
    trends = get_spending_trends(db, uid, months=6)

    # Month nav options (last 12 months)
    month_options = []
    for i in range(11, -1, -1):
        m = (today.month - i - 1) % 12 + 1
        y = today.year + ((today.month - i - 1) // 12)
        month_options.append({'month': m, 'year': y,
                               'label': datetime(y, m, 1).strftime('%B %Y')})

    sidebar_expense, sidebar_pct = get_monthly_spend()

    return render_template('analysis/spending.html',
        spending=spending_data, categories=categories, trends=trends,
        sel_month=sel_month, sel_year=sel_year,
        sel_month_name=start.strftime('%B %Y'),
        month_options=month_options,
        active_tab=active_tab,
        sidebar_expense=sidebar_expense, sidebar_pct=sidebar_pct,
    )


# ─────────────────────────────────────────────
# 3. Budget Performance  /analysis/budget-performance
# ─────────────────────────────────────────────

@analysis_bp.route('/budget-performance', methods=['GET'])
@login_required
def budget_performance():
    db  = get_db()
    uid = session['user_id']

    perf = get_budget_performance(db, uid)
    sidebar_expense, sidebar_pct = get_monthly_spend()

    return render_template('analysis/budget_performance.html',
        perf=perf,
        sidebar_expense=sidebar_expense, sidebar_pct=sidebar_pct,
    )


# ─────────────────────────────────────────────
# 4. Trends & Comparison  /analysis/trends
# ─────────────────────────────────────────────

@analysis_bp.route('/trends', methods=['GET'])
@login_required
def trends():
    db  = get_db()
    uid = session['user_id']

    granularity = request.args.get('granularity', 'monthly')
    compare     = request.args.get('compare', '0') == '1'

    trends_data = get_trends_data(db, uid, granularity=granularity, compare=compare)
    sidebar_expense, sidebar_pct = get_monthly_spend()

    return render_template('analysis/trends.html',
        trends=trends_data, granularity=granularity, compare=compare,
        sidebar_expense=sidebar_expense, sidebar_pct=sidebar_pct,
    )


# ─────────────────────────────────────────────
# 5. Health Score  /analysis/health-score
# ─────────────────────────────────────────────

@analysis_bp.route('/health-score', methods=['GET'])
@login_required
def health_score():
    db  = get_db()
    uid = session['user_id']

    health = compute_health_score(db, uid)
    sidebar_expense, sidebar_pct = get_monthly_spend()

    return render_template('analysis/health_score.html',
        health=health,
        sidebar_expense=sidebar_expense, sidebar_pct=sidebar_pct,
    )
