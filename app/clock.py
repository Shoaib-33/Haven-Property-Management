"""One definition of "today". Servers usually run on UTC, which is behind the local calendar for part of the day,
so set HAVEN_TIMEZONE (for example Europe/Amsterdam) to make Haven use that country's date."""
import os
from datetime import date, datetime


def local_today():
    name = os.environ.get('HAVEN_TIMEZONE', '').strip()
    if name:
        try:
            from zoneinfo import ZoneInfo
            return datetime.now(ZoneInfo(name)).date()
        except Exception:
            pass  # an unknown name falls back to the server's own date rather than breaking the app
    return date.today()
