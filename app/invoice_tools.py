"""Invoice revisions and the company logo.

Issued invoices stay legally traceable: the number, period and tenancy never change, an edit is only possible while no payment or
credit has been booked, the ledger gets an adjustment entry for the difference, and every revision is kept (who, when, why, before/after).
The logo is stored in the database and drawn on the invoice PDF and the invoice preview.
"""
import json
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile

from .db import audit, connect, now, transaction
from .main import FINANCE, date_field, euros, insert, invoice_balance, text_field
from .security import authorize, current_user, record

router = APIRouter()
MAX_LOGO = 1024 * 1024
MAX_ITEMS = 10
# document fields that can be edited right on the invoice; they are stored in the invoice snapshot, so they never touch the company or tenant records
FIELDS = {'company_name': 120, 'company_address': 300, 'tenant_name': 120, 'address': 300, 'object': 200, 'reference': 120,
          'invoice_date': 10, 'from': 10, 'to': 10, 'basic_label': 80, 'service_label': 80}
DEFAULTS = {'basic_label': 'Basic rent', 'service_label': 'Service charge advance'}


def fields_of(inv, snap):
    out = {k: str(snap.get(k) or '') for k in FIELDS}
    out['invoice_date'] = out['invoice_date'] or str(inv['created_at'])[:10]
    out['object'] = out['object'] or ', '.join(x for x in (snap.get('property_name'), snap.get('address')) if x)
    for k, v in DEFAULTS.items():
        out[k] = out[k] or v
    return out


def logo_bytes(company_id):
    with connect() as conn:
        row = conn.execute('SELECT data FROM company_logos WHERE company_id=?', (company_id,)).fetchone()
    return bytes(row['data']) if row else None


@router.get('/api/companies/{company_id}/logo')
def get_logo(company_id: int, user=Depends(current_user)):
    with connect() as conn:
        authorize(conn, user, company_id)
        row = conn.execute('SELECT mime,data FROM company_logos WHERE company_id=?', (company_id,)).fetchone()
    if not row:
        raise HTTPException(404, 'No logo uploaded.')
    return Response(bytes(row['data']), media_type=row['mime'], headers={'Cache-Control': 'private, max-age=3600', 'X-Content-Type-Options': 'nosniff'})


@router.post('/api/companies/{company_id}/logo')
async def set_logo(company_id: int, file: UploadFile = File(...), user=Depends(current_user)):
    content = await file.read(MAX_LOGO + 1)
    await file.close()
    if not content or len(content) > MAX_LOGO:
        raise HTTPException(422, 'Upload a logo between 1 byte and 1 MB.')
    if content.startswith(b'\x89PNG\r\n\x1a\n'):
        mime = 'image/png'
    elif content.startswith(b'\xff\xd8\xff'):
        mime = 'image/jpeg'
    else:
        raise HTTPException(422, 'The logo must be a PNG or JPEG image.')
    with transaction() as conn:
        authorize(conn, user, company_id, ('manager',))
        conn.execute('INSERT INTO company_logos(company_id,mime,data,updated_at) VALUES(?,?,?,?) ON CONFLICT(company_id) DO UPDATE SET mime=excluded.mime,data=excluded.data,updated_at=excluded.updated_at', (company_id, mime, content, now()))
        audit(conn, user['id'], company_id, 'logo_set', 'company', company_id, {'bytes': len(content)})
    return {'ok': True}


@router.delete('/api/companies/{company_id}/logo')
def delete_logo(company_id: int, user=Depends(current_user)):
    with transaction() as conn:
        authorize(conn, user, company_id, ('manager',))
        conn.execute('DELETE FROM company_logos WHERE company_id=?', (company_id,))
        audit(conn, user['id'], company_id, 'logo_removed', 'company', company_id, {})
    return {'ok': True}


def lines_of(inv, snap):
    return {'fields': fields_of(inv, snap), 'basic_cents': inv['basic_cents'], 'service_cents': inv['service_cents'], 'items': snap.get('extra_items', []),
            'due_date': inv['due_date'], 'note': snap.get('note', ''), 'total_cents': inv['total_cents']}


def edit_block(conn, invoice_id):
    booked = conn.execute("SELECT COUNT(*) FROM ledger WHERE invoice_id=? AND kind NOT IN ('invoice','adjustment')", (invoice_id,)).fetchone()[0]
    if booked:
        return 'Payments or credits are already booked on this invoice. Propose a credit note for the difference instead.'
    return None


@router.get('/api/invoices/{invoice_id}/detail')
def invoice_detail(invoice_id: int, user=Depends(current_user)):
    with connect() as conn:
        inv = record(conn, 'invoices', invoice_id, user, FINANCE)
        snap = json.loads(inv['snapshot'])
        revisions = [dict(r) for r in conn.execute('SELECT r.revision,r.reason,r.created_at,r.before_json,r.after_json,u.name editor FROM invoice_revisions r JOIN users u ON u.id=r.edited_by WHERE r.invoice_id=? ORDER BY r.revision DESC', (invoice_id,))]
        block = edit_block(conn, invoice_id)
        balance = invoice_balance(conn, invoice_id)
    for r in revisions:
        r['before'], r['after'] = json.loads(r.pop('before_json')), json.loads(r.pop('after_json'))
    return {'id': inv['id'], 'number': inv['number'], 'period': inv['period'], 'created_at': inv['created_at'], 'balance_cents': balance,
            'current': lines_of(inv, snap), 'tenant_name': snap.get('tenant_name'), 'tenant_email': snap.get('tenant_email'), 'address': snap.get('address'), 'property_name': snap.get('property_name'),
            'released': bool(inv['released_at']), 'fields': fields_of(inv, snap), 'payment_instructions': snap.get('payment_instructions'), 'editable': block is None, 'edit_block': block, 'revisions': revisions}


def signed_euros(value, label):
    try:
        number = Decimal(str(value))
        cents = number * 100
        if not number.is_finite() or abs(number) > 1000000 or cents != cents.to_integral_value():
            raise ValueError()
        return int(cents)
    except (InvalidOperation, ValueError):
        raise HTTPException(422, f'{label} must be a euro amount with at most two decimal places.')


@router.post('/api/invoices/{invoice_id}/edit')
def edit_invoice(invoice_id: int, data: dict, user=Depends(current_user)):
    reason = text_field(data, 'reason', maximum=300)
    note = str(data.get('note') or '').strip()[:500]
    basic = signed_euros(data.get('basic_rent', ''), 'Basic rent')
    service = signed_euros(data.get('service_charge', 0), 'Service charge advance')
    if basic < 0 or service < 0:
        raise HTTPException(422, 'Rent and service charge cannot be negative. Use an extra line for a discount.')
    raw_items = data.get('items') if isinstance(data.get('items'), list) else []
    if len(raw_items) > MAX_ITEMS:
        raise HTTPException(422, f'An invoice can have at most {MAX_ITEMS} extra lines.')
    items = []
    for n, item in enumerate(raw_items, 1):
        item = item if isinstance(item, dict) else {}
        description = str(item.get('description', '')).strip()[:80]
        if not description:
            raise HTTPException(422, f'Extra line {n} needs a description.')
        amount = signed_euros(item.get('amount', ''), f'Extra line {n}')
        if amount == 0:
            raise HTTPException(422, f'Extra line {n} has no amount.')
        items.append({'description': description, 'amount_cents': amount})
    raw_fields = data.get('fields') if isinstance(data.get('fields'), dict) else {}
    fields = {}
    for key, limit in FIELDS.items():
        if key in raw_fields:
            fields[key] = str(raw_fields[key] if raw_fields[key] is not None else '').strip()[:limit]
    for key in ('invoice_date', 'from', 'to'):
        if fields.get(key):
            date_field({key: fields[key]}, key)
    for key in ('company_name', 'tenant_name'):
        if key in fields and not fields[key]:
            raise HTTPException(422, 'The company name and the recipient name cannot be empty.')
    total = basic + service + sum(i['amount_cents'] for i in items)
    if total <= 0:
        raise HTTPException(422, 'The invoice total must stay above zero. To cancel the whole invoice, propose a credit note.')
    with transaction() as conn:
        inv = record(conn, 'invoices', invoice_id, user, FINANCE)
        block = edit_block(conn, invoice_id)
        if block:
            raise HTTPException(409, block)
        snap = json.loads(inv['snapshot'])
        due = date_field(data, 'due_date', inv['due_date'])
        before = lines_of(inv, snap)
        merged = {**before['fields'], **fields}
        after = {'fields': merged, 'basic_cents': basic, 'service_cents': service, 'items': items, 'due_date': due, 'note': note, 'total_cents': total}
        if before == after:
            raise HTTPException(422, 'Nothing changed.')
        snap.update({'basic_cents': basic, 'service_cents': service, 'extra_items': items, 'note': note})
        snap.update({k: v for k, v in merged.items() if v != before['fields'].get(k)})
        conn.execute('UPDATE invoices SET basic_cents=?,service_cents=?,total_cents=?,due_date=?,snapshot=? WHERE id=?', (basic, service, total, due, json.dumps(snap), invoice_id))
        difference = total - inv['total_cents']
        if difference:
            insert(conn, 'ledger', {'company_id': inv['company_id'], 'contract_id': inv['contract_id'], 'invoice_id': invoice_id, 'kind': 'adjustment', 'amount_cents': difference,
                                    'created_by': user['id'], 'created_at': now(), 'note': f'Invoice {inv["number"]} revised: {reason}'[:300]})
        revision = conn.execute('SELECT COALESCE(MAX(revision),0)+1 FROM invoice_revisions WHERE invoice_id=?', (invoice_id,)).fetchone()[0]
        insert(conn, 'invoice_revisions', {'company_id': inv['company_id'], 'invoice_id': invoice_id, 'revision': revision, 'before_json': json.dumps(before), 'after_json': json.dumps(after),
                                           'reason': reason, 'edited_by': user['id'], 'created_at': now()})
        changed = sorted(k for k in merged if merged[k] != before['fields'].get(k))
        audit(conn, user['id'], inv['company_id'], 'revised', 'invoice', invoice_id, {'revision': revision, 'changed_fields': changed, 'total_before': inv['total_cents'], 'total_after': total, 'reason': reason})
        from .portal_access import notify_tenant
        if inv['released_at']:
            notify_tenant(conn, inv['contract_id'], 'Your invoice was updated', f'Invoice {inv["number"]} now totals {euros(total)}, due {due}. Reason: {reason}', 'payments')
        return {'id': invoice_id, 'revision': revision, 'total_cents': total, 'balance_cents': invoice_balance(conn, invoice_id)}


@router.post('/api/invoices/{invoice_id}/email')
def email_invoice(invoice_id: int, user=Depends(current_user)):
    """Queue (or queue again) the invoice email to the tenant, then try to send it right away."""
    from .email_delivery import deliver_pending_emails, queue_invoice, smtp_settings
    if not smtp_settings():
        raise HTTPException(409, 'Email delivery is not set up on this server. Add the SMTP settings first.')
    with transaction() as conn:
        inv = record(conn, 'invoices', invoice_id, user, FINANCE)
        tenant = conn.execute('SELECT t.email FROM contracts c JOIN tenants t ON t.id=c.tenant_id WHERE c.id=?', (inv['contract_id'],)).fetchone()
        job = conn.execute("SELECT * FROM outbound_email_jobs WHERE invoice_id=? AND kind='invoice_issued'", (invoice_id,)).fetchone()
        if job:
            if job['status'] in ('queued', 'sending'):
                raise HTTPException(409, 'This email is already waiting to be sent.')
            conn.execute('UPDATE outbound_email_jobs SET status="queued",attempts=0,next_attempt_at=?,last_error=NULL,sent_at=NULL WHERE id=?', (now(), job['id']))
        elif not queue_invoice(conn, inv, (tenant['email'] if tenant else '') or ''):
            raise HTTPException(422, 'The tenant has no valid email address.')
        audit(conn, user['id'], inv['company_id'], 'email_queued', 'invoice', invoice_id, {'resend': bool(job)})
    result = deliver_pending_emails(company_id=inv['company_id'])
    return {'ok': True, 'result': result}


def release_one(conn, inv, user):
    """Send a draft invoice to the tenant: it becomes visible in the portal, the tenant is alerted and the email is queued."""
    from .email_delivery import queue_invoice
    from .portal_access import notify_staff, notify_tenant
    c = conn.execute('SELECT c.id,c.tenant_id,t.name tenant_name,t.email tenant_email FROM contracts c JOIN tenants t ON t.id=c.tenant_id WHERE c.id=?', (inv['contract_id'],)).fetchone()
    conn.execute('UPDATE invoices SET released_at=? WHERE id=?', (now(), inv['id']))
    queue_invoice(conn, {'id': inv['id'], 'company_id': inv['company_id'], 'contract_id': inv['contract_id']}, c['tenant_email'])
    if conn.execute('SELECT 1 FROM portal_accounts WHERE tenant_id=? AND company_id=? AND enabled=1', (c['tenant_id'], inv['company_id'])).fetchone():
        notify_tenant(conn, c['id'], 'Monthly rent bill ready', f'Invoice {inv["number"]} for {inv["period"]} is available. Amount: {euros(inv["total_cents"])}. Due: {inv["due_date"]}.', 'payments')
    else:
        notify_staff(conn, inv['company_id'], 'Rent bill sent; tenant portal not active', f'{c["tenant_name"]} has a new invoice ({inv["number"]}) but no active tenant portal account to receive the alert.', 'settings')
    audit(conn, user['id'], inv['company_id'], 'released', 'invoice', inv['id'], {'number': inv['number']})


@router.post('/api/invoices/{invoice_id}/release')
def release_invoice(invoice_id: int, user=Depends(current_user)):
    with transaction() as conn:
        inv = record(conn, 'invoices', invoice_id, user, FINANCE)
        if inv['released_at']:
            raise HTTPException(409, 'This invoice was already sent to the tenant.')
        release_one(conn, inv, user)
    from .email_delivery import deliver_pending_emails
    return {'ok': True, 'email': deliver_pending_emails(company_id=inv['company_id'])}


@router.post('/api/invoices/release')
def release_many(data: dict, user=Depends(current_user)):
    """Send every draft invoice of the company (optionally one month) in one go."""
    company_id = int(data.get('company_id') or 0)
    period = str(data.get('period') or '')[:7]
    with transaction() as conn:
        authorize(conn, user, company_id, FINANCE)
        drafts = conn.execute('SELECT * FROM invoices WHERE company_id=? AND released_at IS NULL' + (' AND period=?' if period else '') + ' ORDER BY id', (company_id, *([period] if period else []))).fetchall()
        for row in drafts:
            release_one(conn, dict(row), user)
    from .email_delivery import deliver_pending_emails
    return {'released': len(drafts), 'email': deliver_pending_emails(company_id=company_id)}


@router.post('/api/companies/{company_id}/invoice-review')
def set_invoice_review(company_id: int, data: dict, user=Depends(current_user)):
    on = 1 if data.get('enabled') is True else 0
    with transaction() as conn:
        authorize(conn, user, company_id, ('manager',))
        conn.execute('UPDATE companies SET review_invoices=? WHERE id=?', (on, company_id))
        audit(conn, user['id'], company_id, 'invoice_review_on' if on else 'invoice_review_off', 'company', company_id, {})
    return {'ok': True, 'enabled': bool(on)}
