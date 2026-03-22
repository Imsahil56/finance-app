"""
routes/analysis.py
Analysis blueprint — unified Analysis Workstation with tab-based sub-views.
Old subpage routes redirect to the workstation with appropriate tab state.
"""

from datetime import datetime, date
import calendar

from flask import Blueprint, render_template, redirect, url_for, request, session

from routes.db import get_db, login_required, get_monthly_spend
from services.analytics_service  import (parse_date_range, get_spending_overview,
                                          get_spending_categories, get_spending_trends,
                                          get_budget_performance, get_trends_data,
                                          get_analysis_overview)
from services.health_score_service import compute_health_score
from services.insight_engine       import generate_insights
from services import prediction_service

analysis_bp = Blueprint('analysis', __name__, url_prefix='/analysis')

VALID_TABS = ['overview', 'spending', 'savings', 'debt', 'health', 'loan_payoff']


# ── Main Workstation (all tabs) ───────────────────────────────────────────────

@analysis_bp.route('/', methods=['GET'])
@login_required
def overview():
    db  = get_db()
    uid = session['user_id']
    active_tab = request.args.get('tab', 'overview')
    if active_tab not in VALID_TABS:
        active_tab = 'overview'

    today = date.today()

    # ── Month context (for spending/budget tabs) ──
    try:
        sel_month = int(request.args.get('month', today.month))
        sel_year  = int(request.args.get('year', today.year))
        if not (1 <= sel_month <= 12): raise ValueError
    except (ValueError, TypeError):
        sel_month, sel_year = today.month, today.year

    last_day  = calendar.monthrange(sel_year, sel_month)[1]
    start     = date(sel_year, sel_month, 1)
    end       = date(sel_year, sel_month, last_day)

    # ── Core data (always loaded — used by overview + insights strip) ──
    period = request.args.get('period', 'last_30')
    p_start, p_end = parse_date_range(period, None, None)
    overview_data  = get_analysis_overview(db, uid, p_start, p_end, compare=False)
    health         = compute_health_score(db, uid)
    budget_perf    = get_budget_performance(db, uid)
    insights       = generate_insights(db, uid, overview_data['spending'], budget_perf, health)
    predictions    = prediction_service.get_all_predictions(db, uid, health.get('score', 0))
    fin_status     = prediction_service.get_financial_status(predictions)

    # ── Tab-specific data ──
    spending_data = categories = trends = None
    savings_data  = wealth    = None
    debt_data     = loans     = None
    budget_risks  = None

    if active_tab == 'spending':
        spending_data = get_spending_overview(db, uid, start, end, compare=True)
        categories    = get_spending_categories(db, uid, start, end)
        trends        = get_spending_trends(db, uid, months=6)

    elif active_tab == 'savings':
        savings_data  = prediction_service.predict_savings(db, uid)
        wealth        = prediction_service.predict_wealth_projection(db, uid)
        trends        = get_trends_data(db, uid, granularity='monthly')

    elif active_tab in ('debt', 'loan_payoff'):
        debt_data = prediction_service.get_debt_analytics(db, uid)
        loans     = db.execute(
            "SELECT * FROM loans WHERE user_id=? AND status='active' ORDER BY loan_amount DESC",
            (uid,)
        ).fetchall()

    elif active_tab == 'health':
        pass  # health already loaded above

    # ── Month nav options ──
    month_options = []
    for i in range(11, -1, -1):
        m = (today.month - i - 1) % 12 + 1
        y = today.year + ((today.month - i - 1) // 12)
        month_options.append({'month': m, 'year': y,
                               'label': datetime(y, m, 1).strftime('%B %Y')})

    sidebar_expense, sidebar_pct = get_monthly_spend()

    return render_template('analysis/workstation.html',
        active_tab    = active_tab,
        period        = period,
        # overview
        overview      = overview_data,
        health        = health,
        budget_perf   = budget_perf,
        insights      = insights,
        predictions   = predictions,
        fin_status    = fin_status,
        # spending
        spending      = spending_data,
        categories    = categories,
        trends        = trends,
        sel_month     = sel_month,
        sel_year      = sel_year,
        sel_month_name = start.strftime('%B %Y'),
        month_options = month_options,
        # savings
        savings       = savings_data,
        wealth        = wealth,
        # debt
        debt          = debt_data,
        loans         = loans,
        # budget
        budget_risks  = budget_risks,
        sidebar_expense = sidebar_expense,
        sidebar_pct     = sidebar_pct,
    )


@analysis_bp.route('/forecasting')
@login_required
def forecasting():
    db  = get_db()
    uid = session['user_id']

    forecast    = prediction_service.forecast_month_spend(db, uid)
    savings     = prediction_service.predict_savings(db, uid)
    wealth      = prediction_service.predict_wealth_projection(db, uid)
    budget_risks = prediction_service.predict_budget_risk(db, uid)
    health      = compute_health_score(db, uid)
    debt        = prediction_service.get_debt_analytics(db, uid)

    # 6-month trend for chart
    trends_data = get_trends_data(db, uid, granularity='monthly')

    sidebar_expense, sidebar_pct = get_monthly_spend()
    return render_template('analysis/forecasting.html',
        forecast=forecast, savings=savings, wealth=wealth,
        budget_risks=budget_risks, health=health,
        debt=debt, trends=trends_data,
        sidebar_expense=sidebar_expense, sidebar_pct=sidebar_pct,
    )


@analysis_bp.route('/simulation')
@login_required
def simulation():
    db  = get_db()
    uid = session['user_id']

    savings  = prediction_service.predict_savings(db, uid)
    forecast = prediction_service.forecast_month_spend(db, uid)
    wealth   = prediction_service.predict_wealth_projection(db, uid)
    debt     = prediction_service.get_debt_analytics(db, uid)
    health   = compute_health_score(db, uid)

    sidebar_expense, sidebar_pct = get_monthly_spend()
    return render_template('analysis/simulation.html',
        savings=savings, forecast=forecast, wealth=wealth,
        debt=debt, health=health,
        sidebar_expense=sidebar_expense, sidebar_pct=sidebar_pct,
    )


@analysis_bp.route('/optimization')
@login_required
def optimization():
    db  = get_db()
    uid = session['user_id']

    budget_risks  = prediction_service.predict_budget_risk(db, uid)
    budget_perf   = get_budget_performance(db, uid)
    savings       = prediction_service.predict_savings(db, uid)
    forecast      = prediction_service.forecast_month_spend(db, uid)
    health        = compute_health_score(db, uid)
    debt          = prediction_service.get_debt_analytics(db, uid)
    wealth        = prediction_service.predict_wealth_projection(db, uid)

    sidebar_expense, sidebar_pct = get_monthly_spend()
    return render_template('analysis/optimization.html',
        budget_risks=budget_risks, budget_perf=budget_perf,
        savings=savings, forecast=forecast,
        health=health, debt=debt, wealth=wealth,
        sidebar_expense=sidebar_expense, sidebar_pct=sidebar_pct,
    )


# ── Legacy redirects — keep old URLs alive ────────────────────────────────────

@analysis_bp.route('/spending')
@login_required
def spending():
    return redirect(url_for('analysis.overview', tab='spending'))

@analysis_bp.route('/budget')
@login_required
def budget():
    return redirect(url_for('analysis.overview', tab='overview'))

@analysis_bp.route('/budget-performance')
@login_required
def budget_performance():
    return redirect(url_for('analysis.overview', tab='overview'))

@analysis_bp.route('/savings')
@login_required
def savings():
    return redirect(url_for('analysis.overview', tab='savings'))

@analysis_bp.route('/health-score')
@login_required
def health_score():
    return redirect(url_for('analysis.overview', tab='health'))

@analysis_bp.route('/debt')
@login_required
def debt():
    return redirect(url_for('analysis.overview', tab='debt'))

@analysis_bp.route('/loan-payoff')
@login_required
def loan_payoff():
    return redirect(url_for('analysis.overview', tab='loan_payoff'))

@analysis_bp.route('/trends')
@login_required
def trends():
    return redirect(url_for('analysis.overview', tab='spending'))