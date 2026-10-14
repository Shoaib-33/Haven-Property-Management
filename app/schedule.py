"""Payment dates per tenancy: the payment that is due now and the next scheduled payment.

due   = the oldest invoice with an open balance (what the tenant has to pay, and by when).
next  = the next month with no invoice yet; it is billed automatically, so this is the date the next rent is due and its amount.
"""
import calendar
import os
from datetime import date, timedelta
from .clock import local_today


def month_start(base, offset):
    index = base.year * 12 + base.month - 1 + offset
    return date(index // 12, index % 12 + 1, 1)


def period_amount(conn, contract, first, last):
    """Rent plus service advance for the days of the month the tenancy covers (the same proration as the invoice run)."""
    start = max(first, date.fromisoformat(contract['start_date']))
    end = min(last, date.fromisoformat(contract['end_date'])) if contract['end_date'] else last
    if end < start:
        return None
    versions = conn.execute('SELECT effective_from,basic_cents,service_cents FROM rent_versions WHERE contract_id=? ORDER BY effective_from', (contract['id'],)).fetchall()
    total = 0.0
    for offset in range((end - start).days + 1):
        day = (start + timedelta(days=offset)).isoformat()
        current = None
        for v in versions:
            if v['effective_from'] <= day:
                current = v
        if not current:
            return None
        total += (current['basic_cents'] + current['service_cents']) / last.day
    return int(round(total))


def payment_schedule(conn, contract, today=None):
    today = today or local_today()
    out = {'contract_id': contract['id'], 'due': None, 'next': None}
    due = None
    open_count = open_total = 0
    for inv in conn.execute('SELECT i.id,i.number,i.period,i.due_date,(SELECT COALESCE(SUM(l.amount_cents),0) FROM ledger l WHERE l.invoice_id=i.id) balance '
                            'FROM invoices i WHERE i.contract_id=? AND i.released_at IS NOT NULL ORDER BY i.due_date, i.id', (contract['id'],)):
        if inv['balance'] > 0:
            open_count += 1
            open_total += inv['balance']
            if due is None:
                due = inv
    if due:
        days = (date.fromisoformat(due['due_date']) - today).days
        out['due'] = {'invoice_id': due['id'], 'number': due['number'], 'period': due['period'], 'date': due['due_date'], 'amount_cents': due['balance'], 'days': days,
                      'overdue': days < 0, 'open_count': open_count, 'open_total_cents': open_total}
    if contract['status'] in ('active', 'notice given'):
        for k in range(0, 14):
            first = month_start(today, k)
            last = date(first.year, first.month, calendar.monthrange(first.year, first.month)[1])
            if contract['end_date'] and contract['end_date'] < first.isoformat():
                break
            if contract['start_date'] > last.isoformat():
                continue
            period = first.isoformat()[:7]
            if conn.execute('SELECT 1 FROM invoices WHERE contract_id=? AND period=?', (contract['id'], period)).fetchone():
                continue
            pay_on = max(first, date.fromisoformat(contract['start_date']))
            if pay_on < today:
                continue
            amount = period_amount(conn, contract, first, last)
            if amount:
                try:
                    run_day = max(1, min(28, int(os.environ.get('HAVEN_RENT_BILL_DAY', '15'))))
                except ValueError:
                    run_day = 15
                before = month_start(first, -1)
                issue_on = date(before.year, before.month, run_day)   # the automatic invoice run for this month
                out['next'] = {'period': period, 'date': pay_on.isoformat(), 'amount_cents': amount, 'days': (pay_on - today).days,
                               'issue_on': issue_on.isoformat() if issue_on > today else None}
                break
    return out
