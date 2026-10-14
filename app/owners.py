"""Owners, portfolios and monthly owner statements (the tenant -> manager -> owner money flow).

Haven never touches a bank. Statements are calculated from the verified ledger, approved by a
second person, and the manager records the payout manually once it has been made outside Haven.
"""
import calendar
import io
import json
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from . import db
from .db import audit, connect, now, transaction
from .portal_access import notify
from .security import authorize, current_user, linked, record
from .main import choice, date_field, euros, insert, integer, money, text_field

router = APIRouter()
STAFF = ('manager', 'property', 'finance')
FINANCE = ('manager', 'finance')
EXPENSE_CATEGORIES = ('Repairs & maintenance', 'Insurance', 'Taxes & levies', 'Owners association (VvE)', 'Cleaning & utilities', 'Other')


def _percent(data, key='fee_percent'):
    try:
        value = Decimal(str(data.get(key, '0') or '0'))
        if not value.is_finite() or value < 0 or value > 100:
            raise ValueError()
        return float(value)
    except Exception:
        raise HTTPException(422, 'Management fee must be a percentage between 0 and 100.')


def _email(data):
    email = text_field(data, 'email', maximum=254).lower()
    if '@' not in email:
        raise HTTPException(422, 'Enter a valid email address.')
    return email


@router.post('/api/owners')
def create_owner(data: dict, user=Depends(current_user)):
    company_id = integer(data, 'company_id')
    with transaction() as conn:
        authorize(conn, user, company_id, STAFF)
        oid = insert(conn, 'owners', {'company_id': company_id, 'name': text_field(data, 'name', maximum=120), 'email': _email(data), 'phone': str(data.get('phone', ''))[:50], 'payout_details': str(data.get('payout_details', ''))[:500], 'created_at': now()})
        audit(conn, user['id'], company_id, 'created', 'owner', oid, {})
    return {'id': oid}


@router.patch('/api/owners/{owner_id}')
def edit_owner(owner_id: int, data: dict, user=Depends(current_user)):
    with transaction() as conn:
        owner = record(conn, 'owners', owner_id, user, STAFF)
        values = {'name': text_field(data, 'name', maximum=120), 'email': _email(data), 'phone': str(data.get('phone', ''))[:50], 'payout_details': str(data.get('payout_details', ''))[:500]}
        conn.execute('UPDATE owners SET name=?,email=?,phone=?,payout_details=? WHERE id=?', (*values.values(), owner_id))
        audit(conn, user['id'], owner['company_id'], 'updated', 'owner', owner_id, {'new': {k: v for k, v in values.items() if k != 'payout_details'}})
    return {'ok': True, 'note': 'Portal login email is unchanged.'}


def _assign(conn, company_id, portfolio_id, property_ids):
    ids = list(dict.fromkeys(property_ids))
    if ids:
        marks = ','.join('?' for _ in ids)
        found = conn.execute(f'SELECT id FROM properties WHERE company_id=? AND id IN ({marks})', (company_id, *ids)).fetchall()
        if len(found) != len(ids):
            raise HTTPException(422, 'All selected units must belong to this company.')
    conn.execute('UPDATE properties SET portfolio_id=NULL WHERE portfolio_id=? AND company_id=?', (portfolio_id, company_id))
    for pid in ids:
        conn.execute('UPDATE properties SET portfolio_id=? WHERE id=? AND company_id=?', (portfolio_id, pid, company_id))


def _property_ids(data):
    raw = data.get('property_ids', [])
    if not isinstance(raw, list):
        raise HTTPException(422, 'Choose units as a list.')
    try:
        return [int(x) for x in raw]
    except (TypeError, ValueError):
        raise HTTPException(422, 'Choose valid units.')


@router.post('/api/portfolios')
def create_portfolio(data: dict, user=Depends(current_user)):
    company_id = integer(data, 'company_id')
    with transaction() as conn:
        authorize(conn, user, company_id, STAFF)
        linked(conn, 'owners', integer(data, 'owner_id'), company_id)
        pid = insert(conn, 'portfolios', {'company_id': company_id, 'owner_id': integer(data, 'owner_id'), 'name': text_field(data, 'name', maximum=120), 'fee_percent': _percent(data), 'created_at': now()})
        _assign(conn, company_id, pid, _property_ids(data))
        audit(conn, user['id'], company_id, 'created', 'portfolio', pid, {'fee_percent': data.get('fee_percent')})
    return {'id': pid}


@router.patch('/api/portfolios/{portfolio_id}')
def edit_portfolio(portfolio_id: int, data: dict, user=Depends(current_user)):
    with transaction() as conn:
        p = record(conn, 'portfolios', portfolio_id, user, STAFF)
        owner_id = integer(data, 'owner_id')
        linked(conn, 'owners', owner_id, p['company_id'])
        fee = _percent(data)
        if fee != p['fee_percent'] and conn.execute("SELECT 1 FROM owner_statements WHERE portfolio_id=? AND status='draft'", (portfolio_id,)).fetchone():
            raise HTTPException(409, 'Delete or publish draft statements before changing the management fee.')
        conn.execute('UPDATE portfolios SET name=?,owner_id=?,fee_percent=? WHERE id=?', (text_field(data, 'name', maximum=120), owner_id, fee, portfolio_id))
        if 'property_ids' in data:
            _assign(conn, p['company_id'], portfolio_id, _property_ids(data))
        audit(conn, user['id'], p['company_id'], 'updated', 'portfolio', portfolio_id, {'fee_percent': fee, 'owner_id': owner_id})
    return {'ok': True}


@router.post('/api/owner-expenses')
def add_expense(data: dict, user=Depends(current_user)):
    company_id = integer(data, 'company_id')
    with transaction() as conn:
        authorize(conn, user, company_id, FINANCE)
        portfolio = linked(conn, 'portfolios', integer(data, 'portfolio_id'), company_id)
        property_id = integer(data, 'property_id') if data.get('property_id') else None
        if property_id:
            prop = linked(conn, 'properties', property_id, company_id)
            if prop['portfolio_id'] != portfolio['id']:
                raise HTTPException(422, 'That unit is not part of this portfolio.')
        doc_id = integer(data, 'document_id') if data.get('document_id') else None
        if doc_id:
            linked(conn, 'documents', doc_id, company_id)
        incurred = date_field(data, 'incurred_on')
        if incurred > date.today().isoformat():
            raise HTTPException(422, 'An expense cannot be dated in the future.')
        if conn.execute("SELECT 1 FROM owner_statements WHERE portfolio_id=? AND period=? AND status<>'draft'", (portfolio['id'], incurred[:7])).fetchone():
            raise HTTPException(409, 'A statement for that month is already published. Record the expense in a later month.')
        eid = insert(conn, 'owner_expenses', {'company_id': company_id, 'portfolio_id': portfolio['id'], 'property_id': property_id, 'incurred_on': incurred, 'category': choice(data, 'category', EXPENSE_CATEGORIES), 'description': text_field(data, 'description', maximum=300), 'amount_cents': money(data, 'amount'), 'document_id': doc_id, 'created_by': user['id'], 'created_at': now()})
        audit(conn, user['id'], company_id, 'recorded', 'owner_expense', eid, {'portfolio_id': portfolio['id']})
    return {'id': eid}


def build_statement(conn, portfolio, period):
    """Calculate a statement from verified ledger entries; nothing here moves money."""
    first = date.fromisoformat(period + '-01')
    last = date(first.year, first.month, calendar.monthrange(first.year, first.month)[1])
    props = [dict(r) for r in conn.execute('SELECT id,name,address,city FROM properties WHERE portfolio_id=? AND company_id=? ORDER BY name', (portfolio['id'], portfolio['company_id']))]
    if not props:
        raise HTTPException(422, 'Add at least one unit to this portfolio first.')
    ids = [p['id'] for p in props]
    marks = ','.join('?' for _ in ids)
    billed = dict(conn.execute(f'SELECT c.property_id,SUM(i.total_cents) FROM invoices i JOIN contracts c ON c.id=i.contract_id WHERE c.property_id IN ({marks}) AND i.period=? GROUP BY c.property_id', (*ids, period)).fetchall())
    credits = dict(conn.execute(f"SELECT c.property_id,SUM(-l.amount_cents) FROM ledger l JOIN invoices i ON i.id=l.invoice_id JOIN contracts c ON c.id=l.contract_id WHERE c.property_id IN ({marks}) AND i.period=? AND l.kind='credit_note' GROUP BY c.property_id", (*ids, period)).fetchall())
    collected = dict(conn.execute(f"SELECT c.property_id,SUM(-l.amount_cents) FROM ledger l JOIN contracts c ON c.id=l.contract_id WHERE c.property_id IN ({marks}) AND l.kind IN ('payment','reversal') AND substr(l.created_at,1,7)=? GROUP BY c.property_id", (*ids, period)).fetchall())
    spent = dict(conn.execute(f'SELECT property_id,SUM(amount_cents) FROM owner_expenses WHERE portfolio_id=? AND substr(incurred_on,1,7)=? AND property_id IS NOT NULL GROUP BY property_id', (portfolio['id'], period)).fetchall())
    lines = []
    for p in props:
        contract = conn.execute('SELECT c.id,t.name tenant FROM contracts c JOIN tenants t ON t.id=c.tenant_id WHERE c.property_id=? AND c.start_date<=? AND (c.end_date IS NULL OR c.end_date>=?) ORDER BY c.start_date DESC LIMIT 1', (p['id'], last.isoformat(), last.isoformat())).fetchone()
        lines.append({'property_id': p['id'], 'property': p['name'], 'address': f'{p["address"]}, {p["city"]}', 'tenant': contract['tenant'] if contract else None, 'occupied': bool(contract), 'billed_cents': (billed.get(p['id']) or 0) - (credits.get(p['id']) or 0), 'collected_cents': collected.get(p['id']) or 0, 'expenses_cents': spent.get(p['id']) or 0})
    expenses = [dict(r) for r in conn.execute('SELECT e.id,e.incurred_on,e.category,e.description,e.amount_cents,e.property_id,p.name property FROM owner_expenses e LEFT JOIN properties p ON p.id=e.property_id WHERE e.portfolio_id=? AND substr(e.incurred_on,1,7)=? ORDER BY e.incurred_on,e.id', (portfolio['id'], period))]
    total_collected = sum(l['collected_cents'] for l in lines)
    total_expenses = sum(e['amount_cents'] for e in expenses)
    fee = int((Decimal(total_collected) * Decimal(str(portfolio['fee_percent'])) / Decimal(100)).quantize(Decimal(1), rounding=ROUND_HALF_UP)) if total_collected > 0 else 0
    arrears = []
    today = date.today().isoformat()
    for r in conn.execute(f'SELECT i.id,i.number,i.due_date,i.total_cents,c.property_id,p.name property FROM invoices i JOIN contracts c ON c.id=i.contract_id JOIN properties p ON p.id=c.property_id WHERE c.property_id IN ({marks}) AND i.due_date<? ORDER BY i.due_date', (*ids, today)):
        open_cents = conn.execute('SELECT COALESCE(SUM(amount_cents),0) FROM ledger WHERE invoice_id=?', (r['id'],)).fetchone()[0]
        if open_cents > 0:
            arrears.append({'number': r['number'], 'property': r['property'], 'due_date': r['due_date'], 'open_cents': open_cents})
    owner = conn.execute('SELECT name FROM owners WHERE id=?', (portfolio['owner_id'],)).fetchone()
    snapshot = {'portfolio': portfolio['name'], 'owner': owner['name'] if owner else '', 'period': period, 'fee_percent': portfolio['fee_percent'], 'lines': lines, 'expenses': expenses, 'arrears': arrears, 'units': len(lines), 'occupied_units': sum(1 for l in lines if l['occupied']),
                'method': 'Collected rent is the sum of payments verified by the property manager during the month, less any reversals. The management fee is a percentage of collected rent. Service-charge advances are included in collected rent; costs paid on behalf of the owner are listed as expenses.'}
    return {'collected_cents': total_collected, 'fee_cents': fee, 'expenses_cents': total_expenses, 'net_cents': total_collected - fee - total_expenses, 'snapshot': snapshot}


@router.post('/api/owner-statements/generate')
def generate_statement(data: dict, user=Depends(current_user)):
    company_id = integer(data, 'company_id')
    period = text_field(data, 'period', maximum=7)
    try:
        first = date.fromisoformat(period + '-01')
    except ValueError:
        raise HTTPException(422, 'Period must be YYYY-MM.')
    if first >= date.today().replace(day=1):
        raise HTTPException(422, 'Choose a completed month.')
    with transaction() as conn:
        authorize(conn, user, company_id, FINANCE)
        portfolio = linked(conn, 'portfolios', integer(data, 'portfolio_id'), company_id)
        if conn.execute('SELECT 1 FROM owner_statements WHERE portfolio_id=? AND period=?', (portfolio['id'], period)).fetchone():
            raise HTTPException(409, 'A statement already exists for this portfolio and month. Delete the draft to regenerate it.')
        result = build_statement(conn, portfolio, period)
        sid = insert(conn, 'owner_statements', {'company_id': company_id, 'portfolio_id': portfolio['id'], 'period': period, 'collected_cents': result['collected_cents'], 'fee_cents': result['fee_cents'], 'expenses_cents': result['expenses_cents'], 'net_cents': result['net_cents'], 'snapshot': json.dumps(result['snapshot']), 'created_by': user['id'], 'created_at': now()})
        audit(conn, user['id'], company_id, 'drafted', 'owner_statement', sid, {'period': period, 'net_cents': result['net_cents']})
    return {'id': sid, 'net_cents': result['net_cents']}


@router.delete('/api/owner-statements/{statement_id}')
def delete_draft(statement_id: int, user=Depends(current_user)):
    with transaction() as conn:
        s = record(conn, 'owner_statements', statement_id, user, FINANCE)
        if s['status'] != 'draft':
            raise HTTPException(409, 'Only draft statements can be deleted.')
        conn.execute('DELETE FROM owner_statements WHERE id=?', (statement_id,))
        audit(conn, user['id'], s['company_id'], 'deleted', 'owner_statement', statement_id, {'period': s['period']})
    return {'ok': True}


@router.post('/api/owner-statements/{statement_id}/publish')
def publish_statement(statement_id: int, data: dict, user=Depends(current_user)):
    with transaction() as conn:
        s = record(conn, 'owner_statements', statement_id, user, ('manager',))
        role = authorize(conn, user, s['company_id'], ('manager',))
        if s['status'] != 'draft':
            raise HTTPException(409, 'This statement is already published.')
        if s['created_by'] == user['id'] and role != 'admin':
            raise HTTPException(403, 'A different person must approve the statement before the owner sees it.')
        if data.get('confirmed') is not True:
            raise HTTPException(422, 'Confirm that you checked the collected rent, expenses and fee.')
        conn.execute('UPDATE owner_statements SET status="published",published_at=? WHERE id=?', (now(), statement_id))
        portfolio = conn.execute('SELECT * FROM portfolios WHERE id=?', (s['portfolio_id'],)).fetchone()
        ids = [r[0] for r in conn.execute('SELECT user_id FROM owner_accounts WHERE owner_id=? AND enabled=1', (portfolio['owner_id'],))]
        notify(conn, s['company_id'], ids, 'New rental statement', f'Your statement for {s["period"]} is available.', 'statements')
        audit(conn, user['id'], s['company_id'], 'published', 'owner_statement', statement_id, {'period': s['period']})
    return {'ok': True}


@router.post('/api/owner-statements/{statement_id}/paid')
def mark_paid(statement_id: int, data: dict, user=Depends(current_user)):
    paid_on = date_field(data, 'paid_on')
    reference = text_field(data, 'reference', maximum=150)
    with transaction() as conn:
        s = record(conn, 'owner_statements', statement_id, user, FINANCE)
        if s['status'] != 'published':
            raise HTTPException(409, 'Publish the statement before recording the payout.')
        if paid_on > date.today().isoformat():
            raise HTTPException(422, 'The payout date cannot be in the future.')
        if s['net_cents'] > 0 and data.get('confirmed') is not True:
            raise HTTPException(422, 'Confirm that you made the payout outside Haven.')
        conn.execute('UPDATE owner_statements SET status="paid",paid_on=?,payout_reference=?,paid_by=? WHERE id=?', (paid_on, reference, user['id'], statement_id))
        portfolio = conn.execute('SELECT owner_id FROM portfolios WHERE id=?', (s['portfolio_id'],)).fetchone()
        ids = [r[0] for r in conn.execute('SELECT user_id FROM owner_accounts WHERE owner_id=? AND enabled=1', (portfolio['owner_id'],))]
        notify(conn, s['company_id'], ids, 'Payout recorded', f'The payout for {s["period"]} was recorded on {paid_on}.', 'statements')
        audit(conn, user['id'], s['company_id'], 'payout_recorded', 'owner_statement', statement_id, {'paid_on': paid_on, 'reference': reference})
    return {'ok': True}


def statement_pdf_bytes(s, snap):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import HRFlowable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    from xml.sax.saxutils import escape
    stream = io.BytesIO()
    doc = SimpleDocTemplate(stream, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=20 * mm, bottomMargin=18 * mm, title=f'Rental statement {s["period"]}')
    ink, green, muted, line, pale = colors.HexColor('#19372d'), colors.HexColor('#176b55'), colors.HexColor('#687b74'), colors.HexColor('#dfe9e3'), colors.HexColor('#f1f6f3')
    st = getSampleStyleSheet()
    body = ParagraphStyle('b', parent=st['Normal'], fontSize=9, leading=13, textColor=ink)
    small = ParagraphStyle('s', parent=body, fontSize=8, textColor=muted)
    h2 = ParagraphStyle('h', parent=body, fontName='Helvetica-Bold', fontSize=11, textColor=green, spaceBefore=12, spaceAfter=5)
    title = ParagraphStyle('t', parent=body, fontName='Helvetica-Bold', fontSize=20, leading=25)
    right = ParagraphStyle('r', parent=body, alignment=2)
    P = lambda v, style=body: Paragraph(escape(str(v if v is not None else '')), style)
    story = [P('Rental statement', title), P(f'{snap["portfolio"]} · {snap["period"]} · prepared for {snap["owner"]}', small), Spacer(1, 6), HRFlowable(width='100%', color=line), Spacer(1, 8)]
    status = {'draft': 'DRAFT', 'published': 'Published', 'paid': f'Paid out on {s["paid_on"]} (ref. {s["payout_reference"]})' if s.get('paid_on') else 'Paid'}[s['status']]
    story += [P(f'Status: {status}', small), P('Units', h2)]
    rows = [[P('Unit', small), P('Tenant', small), P('Billed', small), P('Collected', small)]]
    for l in snap['lines']:
        rows.append([P(l['property']), P(l['tenant'] or 'Vacant'), P(euros(l['billed_cents']), right), P(euros(l['collected_cents']), right)])
    grid = TableStyle([('BACKGROUND', (0, 0), (-1, 0), pale), ('LINEBELOW', (0, 0), (-1, -1), .4, line), ('VALIGN', (0, 0), (-1, -1), 'TOP'), ('TOPPADDING', (0, 0), (-1, -1), 5), ('BOTTOMPADDING', (0, 0), (-1, -1), 5)])
    story.append(Table(rows, colWidths=[55 * mm, 55 * mm, 31 * mm, 31 * mm], style=grid, repeatRows=1))
    story.append(P('Expenses paid on your behalf', h2))
    if snap['expenses']:
        er = [[P('Date', small), P('Category', small), P('Description', small), P('Amount', small)]]
        for e in snap['expenses']:
            er.append([P(e['incurred_on']), P(e['category']), P(e['description'] + (f' ({e["property"]})' if e.get('property') else '')), P(euros(e['amount_cents']), right)])
        story.append(Table(er, colWidths=[24 * mm, 40 * mm, 78 * mm, 30 * mm], style=grid, repeatRows=1))
    else:
        story.append(P('No expenses this month.', small))
    summary = [[P('Rent collected'), P(euros(s['collected_cents']), right)], [P(f'Management fee ({snap["fee_percent"]:g}%)'), P('-' + euros(s['fee_cents']), right)], [P('Expenses'), P('-' + euros(s['expenses_cents']), right)], [P('<b>Net result</b>'.replace('<b>', '').replace('</b>', ''), ParagraphStyle('n', parent=body, fontName='Helvetica-Bold')), P(euros(s['net_cents']), ParagraphStyle('n2', parent=right, fontName='Helvetica-Bold'))]]
    story += [Spacer(1, 12), Table(summary, colWidths=[120 * mm, 50 * mm], style=TableStyle([('LINEBELOW', (0, 0), (-1, 2), .4, line), ('BACKGROUND', (0, 3), (-1, 3), pale), ('TOPPADDING', (0, 0), (-1, -1), 6), ('BOTTOMPADDING', (0, 0), (-1, -1), 6)]))]
    if s['net_cents'] < 0:
        story.append(P('A negative net result means expenses exceeded the rent collected; it will be settled with the next payout.', small))
    if snap['arrears']:
        story.append(P('Open rent at the time of preparing this statement', h2))
        ar = [[P('Invoice', small), P('Unit', small), P('Due', small), P('Open', small)]] + [[P(a['number']), P(a['property']), P(a['due_date']), P(euros(a['open_cents']), right)] for a in snap['arrears']]
        story.append(Table(ar, colWidths=[50 * mm, 55 * mm, 33 * mm, 34 * mm], style=grid, repeatRows=1))
    story += [Spacer(1, 12), P(snap['method'], small)]
    doc.build(story)
    return stream.getvalue()


@router.get('/api/owner-statements/{statement_id}/pdf')
def staff_statement_pdf(statement_id: int, user=Depends(current_user)):
    with transaction() as conn:
        s = record(conn, 'owner_statements', statement_id, user, FINANCE)
        audit(conn, user['id'], s['company_id'], 'downloaded', 'owner_statement', statement_id, {})
    return Response(statement_pdf_bytes(s, json.loads(s['snapshot'])), media_type='application/pdf', headers={'Content-Disposition': f'attachment; filename="statement-{s["period"]}-{s["portfolio_id"]}.pdf"'})


# ---------- Owner portal ----------

def owner_accounts(conn, user):
    rows = [dict(r) for r in conn.execute('SELECT o.id,o.name,o.company_id,co.name company_name FROM owner_accounts a JOIN owners o ON o.id=a.owner_id JOIN companies co ON co.id=o.company_id WHERE a.user_id=? AND a.enabled=1', (user['id'],))]
    if not rows:
        raise HTTPException(403, 'Owner access is not enabled for this account.')
    return rows


@router.get('/api/owner/data')
def owner_data(user=Depends(current_user)):
    with connect() as conn:
        accounts = owner_accounts(conn, user)
        owner_ids = [a['id'] for a in accounts]
        marks = ','.join('?' for _ in owner_ids)
        portfolios = [dict(r) for r in conn.execute(f'SELECT id,owner_id,name,fee_percent FROM portfolios WHERE owner_id IN ({marks}) ORDER BY name', owner_ids)]
        pids = [p['id'] for p in portfolios]
        today = date.today().isoformat()
        units, statements, arrears, month_collected = [], [], [], 0
        if pids:
            pm = ','.join('?' for _ in pids)
            units = [dict(r) for r in conn.execute(f'SELECT p.id,p.portfolio_id,p.name,p.address,p.city,p.kind,p.area,p.energy_label FROM properties p WHERE p.portfolio_id IN ({pm}) ORDER BY p.name', pids)]
            for u in units:
                c = conn.execute('SELECT c.start_date,c.end_date,c.status,t.name tenant,(SELECT basic_cents+service_cents FROM rent_versions r WHERE r.contract_id=c.id AND r.effective_from<=? ORDER BY r.effective_from DESC LIMIT 1) rent_cents FROM contracts c JOIN tenants t ON t.id=c.tenant_id WHERE c.property_id=? AND c.start_date<=? AND (c.end_date IS NULL OR c.end_date>=?) ORDER BY c.start_date DESC LIMIT 1', (today, u['id'], today, today)).fetchone()
                u['tenant'] = c['tenant'] if c else None
                u['occupied'] = bool(c)
                u['rent_cents'] = c['rent_cents'] if c else None
                u['lease_end'] = c['end_date'] if c else None
            statements = [dict(r) for r in conn.execute(f"SELECT s.id,s.portfolio_id,s.period,s.collected_cents,s.fee_cents,s.expenses_cents,s.net_cents,s.status,s.published_at,s.paid_on,s.payout_reference,p.name portfolio FROM owner_statements s JOIN portfolios p ON p.id=s.portfolio_id WHERE s.portfolio_id IN ({pm}) AND s.status IN ('published','paid') ORDER BY s.period DESC,s.id DESC", pids)]
            ids = [u['id'] for u in units]
            im = ','.join('?' for _ in ids) or 'NULL'
            for r in conn.execute(f'SELECT i.id,i.number,i.due_date,i.total_cents,p.name property FROM invoices i JOIN contracts c ON c.id=i.contract_id JOIN properties p ON p.id=c.property_id WHERE c.property_id IN ({im}) AND i.due_date<? ORDER BY i.due_date', (*ids, today)):
                open_cents = conn.execute('SELECT COALESCE(SUM(amount_cents),0) FROM ledger WHERE invoice_id=?', (r['id'],)).fetchone()[0]
                if open_cents > 0:
                    arrears.append({'number': r['number'], 'property': r['property'], 'due_date': r['due_date'], 'open_cents': open_cents})
            month_collected = -conn.execute(f"SELECT COALESCE(SUM(l.amount_cents),0) FROM ledger l JOIN contracts c ON c.id=l.contract_id WHERE c.property_id IN ({im}) AND l.kind IN ('payment','reversal') AND substr(l.created_at,1,7)=?", (*ids, today[:7])).fetchone()[0]
        documents = [dict(r) for r in conn.execute(f'SELECT s.id,s.title,s.category,s.from_owner,s.shared_at,d.size,d.mime FROM owner_documents s JOIN documents d ON d.id=s.document_id WHERE s.owner_id IN ({marks}) AND s.revoked=0 ORDER BY s.id DESC', owner_ids)]
        notifications = [dict(r) for r in conn.execute('SELECT id,title,body,target,is_read,created_at FROM notifications WHERE user_id=? ORDER BY id DESC LIMIT 30', (user['id'],))]
    for a in accounts:
        db.audit_view(user['id'], a['company_id'], 'owner_portal', None)
    paid_ytd = sum(s['net_cents'] for s in statements if s['status'] == 'paid' and s['period'][:4] == today[:4])
    return {'accounts': accounts, 'portfolios': portfolios, 'units': units, 'statements': statements, 'arrears': arrears, 'month_collected_cents': month_collected, 'paid_ytd_cents': paid_ytd, 'documents': documents, 'doc_categories': ['Insurance', 'Deed / title', 'Valuation', 'Energy label', 'Inspection report', 'Tax & levies', 'Other'], 'notifications': notifications, 'today': today}


@router.get('/api/owner/statements/{statement_id}/pdf')
def owner_statement_pdf(statement_id: int, user=Depends(current_user)):
    with transaction() as conn:
        accounts = owner_accounts(conn, user)
        s = conn.execute("SELECT s.*,p.owner_id FROM owner_statements s JOIN portfolios p ON p.id=s.portfolio_id WHERE s.id=? AND s.status IN ('published','paid')", (statement_id,)).fetchone()
        if not s or s['owner_id'] not in [a['id'] for a in accounts]:
            raise HTTPException(404, 'Statement not found.')
        s = dict(s)
        audit(conn, user['id'], s['company_id'], 'downloaded', 'owner_statement', statement_id, {})
    return Response(statement_pdf_bytes(s, json.loads(s['snapshot'])), media_type='application/pdf', headers={'Content-Disposition': f'attachment; filename="statement-{s["period"]}.pdf"'})
