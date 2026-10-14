"""Small CBS StatLine client for finalized monthly CPI figures."""
from datetime import date
import json
import re
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from .clock import local_today


BASE = 'https://opendata.cbs.nl/ODataApi/OData/86141ENG'
TABLE = '86141ENG'


class CBSDataError(ValueError):
    pass


def _get_json(path, params=None):
    url = f'{BASE}/{path}'
    if params:
        url += '?' + urlencode(params)
    request = Request(url, headers={'Accept': 'application/json', 'User-Agent': 'HavenPropertyManagement/1.0'})
    try:
        with urlopen(request, timeout=8) as response:
            return json.loads(response.read().decode('utf-8'))
    except (URLError, TimeoutError, OSError, ValueError) as exc:
        raise CBSDataError('CBS CPI data is temporarily unavailable. Please try again later.') from exc


def cpi_annual_rate(period: str):
    """Return CBS headline CPI year-on-year percentage for YYYY-MM, excluding recent provisional months."""
    if not isinstance(period, str) or not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])', period):
        raise CBSDataError('Choose a valid CBS reference month.')
    year, month = map(int, period.split('-'))
    # CBS publishes an initial figure, then a final figure later. Keep a two-full-month
    # buffer so Haven does not use the flash estimate or the most recent provisional data.
    today = local_today()
    cutoff_year, cutoff_month = today.year, today.month - 2
    if cutoff_month <= 0:
        cutoff_year -= 1
        cutoff_month += 12
    if (year, month) > (cutoff_year, cutoff_month):
        raise CBSDataError('Choose a reference month at least two months before today so the CBS index is published and finalized.')

    dimensions = _get_json('ExpenditureCategories', {'$select': 'Key,Title'})
    all_items = [row for row in dimensions.get('value', []) if str(row.get('Title', '')).strip().casefold() == '000000 all items']
    if len(all_items) != 1 or not all_items[0].get('Key'):
        raise CBSDataError('CBS CPI category metadata could not be identified.')
    category_key = str(all_items[0]['Key']).strip().replace("'", "''")
    period_code = f'{year}MM{month:02d}'
    selection = 'Periods,CPI_1,AnnualRateOfChangeCPI_5'
    filter_value = f"substringof('{category_key}',ExpenditureCategories) and Periods eq '{period_code}'"
    result = _get_json('TypedDataSet', {'$select': selection, '$filter': filter_value, '$top': '5'})
    rows = result.get('value', [])
    if len(rows) != 1:
        raise CBSDataError('CBS has no finalized CPI figure for that month. Choose an earlier reference month.')
    row = rows[0]
    try:
        index = float(row['CPI_1'])
        percentage = float(row['AnnualRateOfChangeCPI_5'])
    except (KeyError, TypeError, ValueError):
        raise CBSDataError('CBS has not published a usable CPI figure for that month.')
    if not (-50 <= percentage <= 100) or index <= 0:
        raise CBSDataError('CBS returned an invalid CPI figure.')
    return {'table': TABLE, 'period': period, 'index': index, 'percentage': percentage, 'source_url': f'https://opendata.cbs.nl/statline/#/CBS/en/dataset/{TABLE}/table'}
