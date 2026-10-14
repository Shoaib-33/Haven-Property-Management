"""Paged, searchable lists and a slim bulk payload, so the staff screens stay fast with hundreds of tenants.

The bulk call (/api/data) no longer sends the whole invoice and ledger history. It sends a recent window: the last few billing months,
every invoice that still has an open balance, and anything those screens point at (credit notes, pending receipts). Older records are
reached through the paged endpoints below, and per-month totals for all time are sent as a small summary (monthly_stats).
"""
import json
from datetime import date

from fastapi import APIRouter, Depends, HTTPException

from .db import connect
from .security import authorize, current_user

router = APIRouter()
FINANCE = ('manager', 'finance')
WINDOW_MONTHS = 3
INVOICE_COLUMNS = 'i.id,i.company_id,i.contract_id,i.number,i.period,i.due_date,i.basic_cents,i.service_cents,i.total_cents,i.created_at,i.released_at'
BALANCE = '(SELECT COALESCE(SUM(l.amount_cents),0) FROM ledger l WHERE l.invoice_id=i.id)'


def month_back(today, months):
    index = today.year * 12 + today.month - 1 - months
    return f'{index // 12}-{index % 12 + 1:02d}'


def like(value):
    escaped = value.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
    return f'%{escaped}%'


def page_args(page, page_size):
    page = max(1, int(page or 1))
    page_size = min(200, max(5, int(page_size or 50)))
    return page, page_size, (page - 1) * page_size


def rows_for(conn, sql, params):
    return [dict(r) for r in conn.execute(sql, params)]


def records_for(conn, invoice_ids):
    """Ledger entries and receipts that belong to the given invoices."""
    blob = json.dumps(list(invoice_ids))
    ledger = rows_for(conn, 'SELECT * FROM ledger WHERE invoice_id IN (SELECT value FROM json_each(?)) ORDER BY id DESC', (blob,))
    proofs = rows_for(conn, 'SELECT * FROM payment_proofs WHERE invoice_id IN (SELECT value FROM json_each(?)) ORDER BY id DESC', (blob,))
    return ledger, proofs


def apply_window(conn, company_id, result):
    """Fill result with the recent invoice window and all-time monthly totals (finance roles only)."""
    cut = month_back(date.today(), WINDOW_MONTHS - 1)
    ids = [r[0] for r in conn.execute(
        f'''SELECT i.id FROM invoices i WHERE i.company_id=? AND (i.period>=? OR {BALANCE}>0
            OR i.id IN (SELECT invoice_id FROM credit_notes WHERE company_id=?)
            OR i.id IN (SELECT invoice_id FROM payment_proofs WHERE company_id=? AND status='pending'))''', (company_id, cut, company_id, company_id))]
    blob = json.dumps(ids)
    result['invoices'] = rows_for(conn, f'SELECT {INVOICE_COLUMNS} FROM invoices i WHERE i.id IN (SELECT value FROM json_each(?)) ORDER BY i.id DESC', (blob,))
    result['ledger'] = rows_for(conn, 'SELECT * FROM ledger WHERE company_id=? AND (invoice_id IS NULL OR invoice_id IN (SELECT value FROM json_each(?))) ORDER BY id DESC', (company_id, blob))
    result['payment_proofs'] = rows_for(conn, 'SELECT * FROM payment_proofs WHERE company_id=? AND invoice_id IN (SELECT value FROM json_each(?)) ORDER BY id DESC', (company_id, blob))
    if 'credit_notes' in result:
        result['credit_notes'] = [c for c in result['credit_notes'] if c['invoice_id'] in set(ids)]
    total = conn.execute('SELECT COUNT(*) FROM invoices WHERE company_id=?', (company_id,)).fetchone()[0]
    result['invoice_window'] = {'from': cut, 'loaded': len(ids), 'total': total}
    result['monthly_stats'] = rows_for(conn, f'''
        WITH b AS (SELECT i.period, i.total_cents,
                          {BALANCE} AS bal,
                          (SELECT COALESCE(SUM(l.amount_cents),0) FROM ledger l WHERE l.invoice_id=i.id AND l.kind='credit_note') AS credit,
                          (SELECT COALESCE(SUM(l.amount_cents),0) FROM ledger l WHERE l.invoice_id=i.id AND l.kind IN ('payment','reversal')) AS paid_in
                   FROM invoices i WHERE i.company_id=?)
        SELECT period, COUNT(*) AS invoices, SUM(total_cents+credit) AS billed, -SUM(paid_in) AS collected, SUM(CASE WHEN bal<=0 THEN 1 ELSE 0 END) AS paid
        FROM b GROUP BY period ORDER BY period''', (company_id,))


def finance_company(conn, user, company_id):
    authorize(conn, user, company_id, FINANCE)


@router.get('/api/invoices')
def list_invoices(company_id: int, q: str = '', status: str = 'All', period: str = '', email: str = 'All', page: int = 1, page_size: int = 50, user=Depends(current_user)):
    page, page_size, offset = page_args(page, page_size)
    status = status if status in ('Paid', 'Partially paid', 'Overdue', 'Unpaid') else ''
    with connect() as conn:
        finance_company(conn, user, company_id)
        where, params = ['i.company_id=?'], [company_id]
        if period:
            where.append('i.period=?')
            params.append(period[:7])
        if q.strip():
            where.append("(i.number LIKE ? ESCAPE '\\' OR t.name LIKE ? ESCAPE '\\' OR p.name LIKE ? ESCAPE '\\' OR t.email LIKE ? ESCAPE '\\')")
            params += [like(q.strip())] * 4
        job = lambda col: f"(SELECT j.{col} FROM outbound_email_jobs j WHERE j.invoice_id=i.id AND j.kind='invoice_issued')"
        base = f'''SELECT {INVOICE_COLUMNS}, t.name AS tenant_name, t.email AS tenant_email, p.name AS property_name, {BALANCE} AS balance_cents,
                   {job('id')} AS email_job_id, {job('status')} AS email_status, {job('sent_at')} AS email_sent_at, {job('last_error')} AS email_error, {job('recipient')} AS email_to,
                   CASE WHEN {BALANCE}<=0 THEN 'Paid' WHEN {BALANCE}<i.total_cents THEN 'Partially paid' WHEN i.due_date<? THEN 'Overdue' ELSE 'Unpaid' END AS status
                   FROM invoices i JOIN contracts c ON c.id=i.contract_id JOIN tenants t ON t.id=c.tenant_id JOIN properties p ON p.id=c.property_id
                   WHERE {' AND '.join(where)}'''
        today = date.today().isoformat()
        email = email.lower() if email.lower() in ('sent', 'queued', 'failed') else ('none' if email == 'Not emailed' else 'draft' if email == 'Drafts' else '')
        sql = f'SELECT * FROM ({base}) WHERE 1=1' + (' AND status=?' if status else '') + (' AND released_at IS NULL' if email == 'draft' else ' AND released_at IS NOT NULL AND email_status IS NULL' if email == 'none' else ' AND email_status=?' if email else '')
        args = [today, *params] + ([status] if status else []) + ([email] if email and email not in ('none', 'draft') else [])
        total = conn.execute(f'SELECT COUNT(*) FROM ({sql})', args).fetchone()[0]
        items = rows_for(conn, f'{sql} ORDER BY due_date DESC, id DESC LIMIT ? OFFSET ?', (*args, page_size, offset))
        ledger, proofs = records_for(conn, [i['id'] for i in items])
    from .email_delivery import smtp_settings
    configured = bool(smtp_settings())
    for i in items:
        if i['released_at'] is None:
            i['email_note'] = 'Draft: not sent to the tenant yet'
        elif i['email_status'] is None:
            i['email_note'] = ('Email is not set up' if not configured else 'Tenant has no valid email' if '@' not in (i['tenant_email'] or '') else 'Not sent yet')
        i.pop('tenant_email', None)
    return {'items': items, 'ledger': ledger, 'payment_proofs': proofs, 'email_configured': configured, 'total': total, 'page': page, 'page_size': page_size, 'pages': max(1, -(-total // page_size))}


@router.get('/api/periods/{period}')
def period_records(period: str, company_id: int, user=Depends(current_user)):
    if len(period) != 7 or period[4] != '-':
        raise HTTPException(422, 'Use a month like 2026-03.')
    with connect() as conn:
        finance_company(conn, user, company_id)
        invoices = rows_for(conn, f'SELECT {INVOICE_COLUMNS} FROM invoices i WHERE i.company_id=? AND i.period=? ORDER BY i.id DESC', (company_id, period))
        ledger, proofs = records_for(conn, [i['id'] for i in invoices])
    return {'invoices': invoices, 'ledger': ledger, 'payment_proofs': proofs}


@router.get('/api/ledger')
def list_ledger(company_id: int, q: str = '', kind: str = 'All', page: int = 1, page_size: int = 50, user=Depends(current_user)):
    page, page_size, offset = page_args(page, page_size)
    with connect() as conn:
        finance_company(conn, user, company_id)
        where, params = ['l.company_id=?'], [company_id]
        if kind != 'All':
            where.append('l.kind=?')
            params.append(kind[:40])
        if q.strip():
            where.append("(t.name LIKE ? ESCAPE '\\' OR l.note LIKE ? ESCAPE '\\' OR i.number LIKE ? ESCAPE '\\')")
            params += [like(q.strip())] * 3
        base = f'''FROM ledger l LEFT JOIN contracts c ON c.id=l.contract_id LEFT JOIN tenants t ON t.id=c.tenant_id LEFT JOIN invoices i ON i.id=l.invoice_id WHERE {' AND '.join(where)}'''
        total = conn.execute(f'SELECT COUNT(*) {base}', params).fetchone()[0]
        items = rows_for(conn, f'SELECT l.id,l.contract_id,l.invoice_id,l.kind,l.amount_cents,l.created_at,l.note,t.name AS tenant_name,i.number AS invoice_number {base} ORDER BY l.id DESC LIMIT ? OFFSET ?', (*params, page_size, offset))
        kinds = [r[0] for r in conn.execute('SELECT DISTINCT kind FROM ledger WHERE company_id=? ORDER BY kind', (company_id,))]
    return {'items': items, 'kinds': kinds, 'total': total, 'page': page, 'page_size': page_size, 'pages': max(1, -(-total // page_size))}


@router.get('/api/audit')
def list_audit(company_id: int, q: str = '', page: int = 1, page_size: int = 50, user=Depends(current_user)):
    page, page_size, offset = page_args(page, page_size)
    with connect() as conn:
        authorize(conn, user, company_id, ('manager',))
        where, params = ['a.company_id=?'], [company_id]
        if q.strip():
            where.append("(a.action LIKE ? ESCAPE '\\' OR a.entity LIKE ? ESCAPE '\\' OR u.name LIKE ? ESCAPE '\\' OR CAST(a.entity_id AS TEXT)=?)")
            params += [like(q.strip().replace(' ', '_'))[:80], like(q.strip()), like(q.strip()), q.strip()]
        base = f'FROM audit a LEFT JOIN users u ON u.id=a.user_id WHERE {" AND ".join(where)}'
        total = conn.execute(f'SELECT COUNT(*) {base}', params).fetchone()[0]
        items = rows_for(conn, f'SELECT a.id,a.created_at,a.action,a.entity,a.entity_id,a.detail,a.hash,u.name AS user_name {base} ORDER BY a.id DESC LIMIT ? OFFSET ?', (*params, page_size, offset))
    return {'items': items, 'total': total, 'page': page, 'page_size': page_size, 'pages': max(1, -(-total // page_size))}
