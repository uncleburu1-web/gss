from datetime import datetime, timedelta
from django.utils import timezone


def parse_period(request):
    """
    Reads ?date=YYYY-MM-DD or ?month=YYYY-MM from the request and returns
    (start, end, label, granularity) as timezone-aware datetimes.
    Defaults to "today" when neither is given.
    granularity is 'day' (hour-by-hour) or 'month' (day-by-day).
    """
    tz = timezone.get_current_timezone()
    date_param = request.query_params.get('date')
    month_param = request.query_params.get('month')

    if month_param:
        year, month = map(int, month_param.split('-'))
        start = datetime(year, month, 1, tzinfo=tz)
        if month == 12:
            end = datetime(year + 1, 1, 1, tzinfo=tz)
        else:
            end = datetime(year, month + 1, 1, tzinfo=tz)
        return start, end, month_param, 'month'

    if date_param:
        year, month, day = map(int, date_param.split('-'))
        start = datetime(year, month, day, tzinfo=tz)
        end = start + timedelta(days=1)
        return start, end, date_param, 'day'

    today = timezone.localdate()
    start = datetime(today.year, today.month, today.day, tzinfo=tz)
    end = start + timedelta(days=1)
    return start, end, today.isoformat(), 'day'


def parse_range_period(request):
    """
    Reads ?period=today|yesterday|this_week|last_week|this_month|last_month|
    this_year|custom (default: this_month), and for 'custom', ?start=
    YYYY-MM-DD&end=YYYY-MM-DD (end inclusive). Returns a dict with the
    selected [start, end) range plus [previous_start, previous_end) — the
    immediately-preceding period of the SAME length — which is the
    baseline every period-over-period "+18% vs last period" figure in
    Executive/Expense/Liability Analytics is measured against (see
    reports/analytics.py's pct_change).

    Deliberately a separate function from parse_period above rather than
    an extension of it: that one is only ever called with a single day or
    a single month and several older report views depend on exactly that
    (start, end, label, granularity) shape, so broadening it in place
    risked changing behaviour they already rely on for no benefit. This
    one is purely additive.
    """
    tz = timezone.get_current_timezone()
    period = request.query_params.get('period', 'this_month')
    today = timezone.localdate()

    def midnight(d):
        return datetime(d.year, d.month, d.day, tzinfo=tz)

    if period == 'today':
        start_date, end_date_exclusive, label = today, today + timedelta(days=1), 'Today'
    elif period == 'yesterday':
        start_date = today - timedelta(days=1)
        end_date_exclusive, label = today, 'Yesterday'
    elif period == 'this_week':
        start_date = today - timedelta(days=today.weekday())  # Monday
        end_date_exclusive, label = start_date + timedelta(days=7), 'This week'
    elif period == 'last_week':
        this_week_start = today - timedelta(days=today.weekday())
        start_date = this_week_start - timedelta(days=7)
        end_date_exclusive, label = this_week_start, 'Last week'
    elif period == 'this_month':
        start_date = today.replace(day=1)
        end_date_exclusive = (start_date + timedelta(days=32)).replace(day=1)
        label = 'This month'
    elif period == 'last_month':
        this_month_start = today.replace(day=1)
        end_date_exclusive = this_month_start
        start_date = (this_month_start - timedelta(days=1)).replace(day=1)
        label = 'Last month'
    elif period == 'this_year':
        start_date = today.replace(month=1, day=1)
        end_date_exclusive = today.replace(year=today.year + 1, month=1, day=1)
        label = 'This year'
    elif period == 'custom':
        start_str = request.query_params.get('start')
        end_str = request.query_params.get('end')
        if not start_str or not end_str:
            raise ValueError('A custom period needs both ?start= and ?end= (YYYY-MM-DD).')
        start_date = datetime.strptime(start_str, '%Y-%m-%d').date()
        end_date_inclusive = datetime.strptime(end_str, '%Y-%m-%d').date()
        if end_date_inclusive < start_date:
            raise ValueError('The end date must be on or after the start date.')
        end_date_exclusive = end_date_inclusive + timedelta(days=1)
        label = f'{start_str} to {end_str}'
    else:
        raise ValueError(f'Unknown period "{period}".')

    start = midnight(start_date)
    end = midnight(end_date_exclusive)
    length = end - start
    return {
        'start': start, 'end': end, 'label': label,
        'previous_start': start - length, 'previous_end': start,
    }
