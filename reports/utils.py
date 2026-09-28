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
