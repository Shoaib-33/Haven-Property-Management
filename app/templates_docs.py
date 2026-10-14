"""Letter and lease templates with merge fields and user-defined custom fields, rendered to PDF."""
import io
import json
import re
from datetime import date
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from .db import audit, now, transaction
from .main import choice, date_field, euros, insert, integer, save_document, text_field
from .portal_access import notify_tenant, share_document
from .lease_starter import BODY as LEASE_BODY, NAME as LEASE_NAME
from .security import authorize, current_user, linked, record

router = APIRouter()
STAFF = ('manager', 'property', 'finance')
ENTITY_TABLES = {'tenant': 'tenants', 'property': 'properties', 'contract': 'contracts', 'owner': 'owners'}
PLACEHOLDER = re.compile(r'\{\{\s*([a-z_]+(?:\.[a-z_0-9]+)?)\s*\}\}')

BUILTIN_KEYS = ['today', 'company.name', 'company.address', 'company.kvk', 'user.name', 'tenant.name', 'tenant.email', 'tenant.phone',
                'property.name', 'property.address', 'property.city', 'property.postcode', 'property.area', 'property.rooms', 'property.energy_label',
                'contract.start_date', 'contract.end_date', 'contract.signed_on', 'contract.deposit', 'contract.term', 'contract.term_en', 'company.payment_instructions', 'rent.basic', 'rent.service', 'rent.total',
                'owner.name', 'arrears.count', 'arrears.total', 'arrears.oldest_due']

STARTERS = [
    ('Friendly rent reminder', 'Letter', """# Rent reminder

{{company.name}}
{{today}}

Dear {{tenant.name}},

Our records show that {{arrears.count}} rent invoice(s) for {{property.address}} are still open, together {{arrears.total}}. The oldest was due on {{arrears.oldest_due}}.

If you have already paid, or have sent proof of payment through the tenant portal, please ignore this letter while we check it. If something is unclear, or you would like to talk about a payment arrangement, please contact us as soon as possible.

This is a friendly reminder and not a formal legal notice.

Kind regards,
{{user.name}}
{{company.name}}"""),
    ('Move-out confirmation', 'Notice', """# Confirmation of move-out

{{company.name}}
{{today}}

Dear {{tenant.name}},

We confirm that your tenancy of {{property.address}}, {{property.postcode}} {{property.city}}, ends on {{contract.end_date}}.

- A move-out inspection will be planned with you before the end date.
- Your deposit of {{contract.deposit}} will be reviewed after the inspection. Any deduction will be explained in writing with supporting evidence.
- Please return all keys on the final day.

Kind regards,
{{user.name}}
{{company.name}}"""),
    (LEASE_NAME, 'Lease', LEASE_BODY),
]


def build_context(conn, company_id, contract_id, user):
    contract = linked(conn, 'contracts', contract_id, company_id)
    tenant = linked(conn, 'tenants', contract['tenant_id'], company_id)
    prop = linked(conn, 'properties', contract['property_id'], company_id)
    company = conn.execute('SELECT * FROM companies WHERE id=?', (company_id,)).fetchone()
    rent = conn.execute('SELECT * FROM rent_versions WHERE contract_id=? AND effective_from<=? ORDER BY effective_from DESC LIMIT 1', (contract_id, date.today().isoformat())).fetchone()
    if not rent:
        rent = conn.execute('SELECT * FROM rent_versions WHERE contract_id=? ORDER BY effective_from LIMIT 1', (contract_id,)).fetchone()
    ctx = {'today': date.today().isoformat(), 'company.name': company['name'], 'company.address': company['address'], 'company.kvk': company['kvk'], 'user.name': user['name'],
           'tenant.name': tenant['name'], 'tenant.email': tenant['email'], 'tenant.phone': tenant['phone'],
           'property.name': prop['name'], 'property.address': prop['address'], 'property.city': prop['city'], 'property.postcode': prop['postcode'], 'property.area': f'{prop["area"]:g}', 'property.rooms': str(prop['rooms']), 'property.energy_label': prop['energy_label'],
           'contract.start_date': contract['start_date'], 'contract.end_date': contract['end_date'] or 'open-ended', 'contract.signed_on': contract['signed_on'], 'contract.deposit': euros(contract['deposit_cents'])}
    end = contract['end_date']
    ctx.update({'company.payment_instructions': (company['payment_instructions'] or '').strip(),
                'contract.term': f'bepaalde tijd, eindigend op {end}' if end else 'onbepaalde tijd',
                'contract.term_en': f'a fixed term ending on {end}' if end else 'an indefinite period'})
    if rent:
        ctx.update({'rent.basic': euros(rent['basic_cents']), 'rent.service': euros(rent['service_cents']), 'rent.total': euros(rent['basic_cents'] + rent['service_cents'])})
    owner = conn.execute('SELECT o.* FROM owners o JOIN portfolios p ON p.owner_id=o.id WHERE p.id=?', (prop['portfolio_id'],)).fetchone() if prop['portfolio_id'] else None
    if owner:
        ctx['owner.name'] = owner['name']
    open_invoices = []
    today = date.today().isoformat()
    for inv in conn.execute('SELECT id,due_date FROM invoices WHERE contract_id=? AND due_date<? AND released_at IS NOT NULL ORDER BY due_date', (contract_id, today)):
        balance = conn.execute('SELECT COALESCE(SUM(amount_cents),0) FROM ledger WHERE invoice_id=?', (inv['id'],)).fetchone()[0]
        if balance > 0:
            open_invoices.append((inv['due_date'], balance))
    ctx['arrears.count'] = str(len(open_invoices))
    ctx['arrears.total'] = euros(sum(b for _, b in open_invoices))
    if open_invoices:
        ctx['arrears.oldest_due'] = open_invoices[0][0]
    entity_ids = {'tenant': tenant['id'], 'property': prop['id'], 'contract': contract_id, 'owner': owner['id'] if owner else None}
    for f in conn.execute('SELECT id,entity,key FROM custom_fields WHERE company_id=?', (company_id,)):
        if entity_ids.get(f['entity']) is None:
            continue
        row = conn.execute('SELECT value FROM custom_values WHERE field_id=? AND entity_id=?', (f['id'], entity_ids[f['entity']])).fetchone()
        if row and row['value'] != '':
            ctx[f'{f["entity"]}.{f["key"]}'] = row['value']
    return ctx


def render(body, ctx):
    missing = []

    def fill(m):
        key = m.group(1)
        if key in ctx and ctx[key] != '':
            return str(ctx[key])
        if key not in missing:
            missing.append(key)
        return m.group(0)
    return PLACEHOLDER.sub(fill, body), missing


def explain(missing):
    notes = []
    if any(k.startswith('arrears.') for k in missing):
        notes.append('This tenancy has no overdue invoices, so this letter does not apply to it. Choose a tenancy with open rent.')
    if any(k.startswith('rent.') for k in missing):
        notes.append('This tenancy has no rent history yet.')
    if any(k == 'owner.name' for k in missing):
        notes.append('The property is not part of an owner portfolio.')
    if any(k.split('.')[0] in ENTITY_TABLES and k not in BUILTIN_KEYS for k in missing):
        notes.append('Add the missing values under Templates & fields > Edit data.')
    return notes


def pdf_bytes(title, text, signatures=None):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import HRFlowable, ListFlowable, ListItem, Paragraph, SimpleDocTemplate, Spacer
    from xml.sax.saxutils import escape
    stream = io.BytesIO()
    doc = SimpleDocTemplate(stream, pagesize=A4, leftMargin=24 * mm, rightMargin=24 * mm, topMargin=22 * mm, bottomMargin=20 * mm, title=title)
    ink, green = colors.HexColor('#19372d'), colors.HexColor('#176b55')
    base = getSampleStyleSheet()
    body = ParagraphStyle('b', parent=base['Normal'], fontSize=10.5, leading=15.5, textColor=ink, spaceAfter=6)
    h1 = ParagraphStyle('h1', parent=body, fontName='Helvetica-Bold', fontSize=20, leading=25, spaceAfter=10)
    h2 = ParagraphStyle('h2', parent=body, fontName='Helvetica-Bold', fontSize=12, textColor=green, spaceBefore=10, spaceAfter=4)
    story = []
    para, bullets = [], []

    def flush():
        if para:
            story.append(Paragraph('<br/>'.join(escape(l) for l in para), body))
            para.clear()
        if bullets:
            story.append(ListFlowable([ListItem(Paragraph(escape(b), body), leftIndent=12) for b in bullets], bulletType='bullet', start='•', leftIndent=14))
            bullets.clear()
    for raw in text.strip().split(chr(10)):
        line = raw.rstrip()
        if not line.strip():
            flush()
        elif line.startswith('# '):
            flush()
            story += [Paragraph(escape(line[2:]), h1), HRFlowable(width='100%', color=colors.HexColor('#dfe9e3')), Spacer(1, 8)]
        elif line.startswith('## '):
            flush()
            story.append(Paragraph(escape(line[3:]), h2))
        elif line.startswith('- '):
            if para:
                flush()
            bullets.append(line[2:])
        else:
            if bullets:
                flush()
            para.append(line)
    flush()
    if signatures:
        # signature block: one cell per signer with the drawn, typed or uploaded signature image above a line and the caption
        from reportlab.lib.utils import ImageReader
        from reportlab.platypus import Image, KeepTogether, Table, TableStyle
        muted = colors.HexColor('#687b74')
        cap = ParagraphStyle('cap', parent=body, fontSize=8.5, leading=12, textColor=muted, spaceAfter=0)
        cells = []
        for sig in signatures:
            parts = []
            if sig.get('image'):
                iw, ih = ImageReader(io.BytesIO(sig['image'])).getSize()
                scale = min(62 * mm / iw, 24 * mm / ih)
                parts.append(Image(io.BytesIO(sig['image']), width=iw * scale, height=ih * scale, hAlign='LEFT'))
            else:
                parts.append(Spacer(1, 18 * mm))
            parts += [HRFlowable(width='92%', thickness=0.7, color=colors.HexColor('#9aaba4'), hAlign='LEFT'),
                      Paragraph('<b>' + escape(sig['label']) + '</b><br/>' + escape(sig['caption']).replace(chr(10), '<br/>'), cap)]
            cells.append(parts)
        table = Table([cells], colWidths=[(doc.width / len(cells))] * len(cells), style=TableStyle([('VALIGN', (0, 0), (-1, -1), 'BOTTOM'), ('LEFTPADDING', (0, 0), (-1, -1), 0)]))
        story += [Spacer(1, 14), KeepTogether([Paragraph('Signatures', h2), table])]
    doc.build(story)
    return stream.getvalue()


def _body(data):
    body = text_field(data, 'body', maximum=20000)
    for key in PLACEHOLDER.findall(body):
        if key != 'today' and '.' not in key:
            raise HTTPException(422, f'Unknown merge field {{{{{key}}}}}. Use names like tenant.name.')
    return body


@router.post('/api/templates')
def create_template(data: dict, user=Depends(current_user)):
    company_id = integer(data, 'company_id')
    with transaction() as conn:
        authorize(conn, user, company_id, STAFF)
        tid = insert(conn, 'doc_templates', {'company_id': company_id, 'name': text_field(data, 'name', maximum=120), 'kind': choice(data, 'kind', ('Lease', 'Letter', 'Notice', 'Other')), 'body': _body(data), 'created_by': user['id'], 'created_at': now()})
        audit(conn, user['id'], company_id, 'created', 'template', tid, {})
    return {'id': tid}


@router.patch('/api/templates/{template_id}')
def edit_template(template_id: int, data: dict, user=Depends(current_user)):
    with transaction() as conn:
        t = record(conn, 'doc_templates', template_id, user, STAFF)
        conn.execute('UPDATE doc_templates SET name=?,kind=?,body=? WHERE id=?', (text_field(data, 'name', maximum=120), choice(data, 'kind', ('Lease', 'Letter', 'Notice', 'Other')), _body(data), template_id))
        audit(conn, user['id'], t['company_id'], 'updated', 'template', template_id, {})
    return {'ok': True}


@router.post('/api/templates/{template_id}/archive')
def archive_template(template_id: int, data: dict, user=Depends(current_user)):
    with transaction() as conn:
        t = record(conn, 'doc_templates', template_id, user, STAFF)
        conn.execute('UPDATE doc_templates SET archived=? WHERE id=?', (0 if data.get('archived') is False else 1, template_id))
        audit(conn, user['id'], t['company_id'], 'archived', 'template', template_id, {})
    return {'ok': True}


@router.post('/api/templates/starters')
def add_starters(data: dict, user=Depends(current_user)):
    company_id = integer(data, 'company_id')
    added = 0
    with transaction() as conn:
        authorize(conn, user, company_id, STAFF)
        for name, kind, body in STARTERS:
            if not conn.execute('SELECT 1 FROM doc_templates WHERE company_id=? AND name=?', (company_id, name)).fetchone():
                insert(conn, 'doc_templates', {'company_id': company_id, 'name': name, 'kind': kind, 'body': body, 'created_by': user['id'], 'created_at': now()})
                added += 1
        audit(conn, user['id'], company_id, 'starters_added', 'template', None, {'count': added})
    return {'added': added}


@router.post('/api/templates/{template_id}/preview')
def preview(template_id: int, data: dict, user=Depends(current_user)):
    with transaction() as conn:
        t = record(conn, 'doc_templates', template_id, user, STAFF)
        ctx = build_context(conn, t['company_id'], integer(data, 'contract_id'), user)
    text, missing = render(t['body'], ctx)
    return {'text': text, 'missing': missing, 'hints': explain(missing)}


@router.post('/api/templates/{template_id}/generate')
def generate(template_id: int, data: dict, user=Depends(current_user)):
    contract_id = integer(data, 'contract_id')
    save, share = data.get('save') is True, data.get('share') is True
    with transaction() as conn:
        t = record(conn, 'doc_templates', template_id, user, STAFF)
        if share and not save:
            raise HTTPException(422, 'Save the document to the vault before sharing it with the tenant.')
        ctx = build_context(conn, t['company_id'], contract_id, user)
        text, missing = render(t['body'], ctx)
        if missing:
            raise HTTPException(422, 'Fill in these fields first: ' + ', '.join(missing) + '. ' + ' '.join(explain(missing)))
        pdf = pdf_bytes(t['name'], text)
        filename = re.sub(r'[^A-Za-z0-9._-]+', '-', f'{t["name"]}-{contract_id}-{date.today().isoformat()}').strip('-') + '.pdf'
        doc_id = None
        if save:
            doc_id = save_document(conn, t['company_id'], contract_id, user['id'], 'Lease draft' if t['kind'] == 'Lease' else 'Generated letter', (pdf, 'application/pdf', '.pdf', filename))
            if share:
                contract = conn.execute('SELECT id FROM contracts WHERE id=?', (contract_id,)).fetchone()
                share_document(conn, t['company_id'], contract['id'], doc_id, user['id'])
                notify_tenant(conn, contract_id, 'New document available', t['name'], 'documents')
        audit(conn, user['id'], t['company_id'], 'generated', 'template', template_id, {'contract_id': contract_id, 'document_id': doc_id, 'shared': share})
    if save:
        return {'document_id': doc_id, 'shared': share}
    return Response(pdf, media_type='application/pdf', headers={'Content-Disposition': f'attachment; filename="{filename}"'})


# ---------- Custom fields ----------

@router.post('/api/custom-fields')
def create_field(data: dict, user=Depends(current_user)):
    company_id = integer(data, 'company_id')
    key = text_field(data, 'key', maximum=40).lower()
    if not re.fullmatch(r'[a-z][a-z0-9_]{1,39}', key):
        raise HTTPException(422, 'The key must start with a letter and use only lowercase letters, digits and underscores.')
    entity = choice(data, 'entity', tuple(ENTITY_TABLES))
    if f'{entity}.{key}' in BUILTIN_KEYS:
        raise HTTPException(422, 'That name is already a built-in merge field. Choose another key.')
    with transaction() as conn:
        authorize(conn, user, company_id, ('manager', 'property'))
        fid = insert(conn, 'custom_fields', {'company_id': company_id, 'entity': entity, 'key': key, 'label': text_field(data, 'label', maximum=80), 'field_type': choice(data, 'field_type', ('text', 'number', 'date'), 'text'), 'created_at': now()})
        audit(conn, user['id'], company_id, 'created', 'custom_field', fid, {'entity': entity, 'key': key})
    return {'id': fid}


@router.delete('/api/custom-fields/{field_id}')
def delete_field(field_id: int, user=Depends(current_user)):
    with transaction() as conn:
        f = record(conn, 'custom_fields', field_id, user, ('manager',))
        conn.execute('DELETE FROM custom_fields WHERE id=?', (field_id,))
        audit(conn, user['id'], f['company_id'], 'deleted', 'custom_field', field_id, {'key': f['key']})
    return {'ok': True}


@router.post('/api/custom-values')
def set_values(data: dict, user=Depends(current_user)):
    company_id = integer(data, 'company_id')
    entity = choice(data, 'entity', tuple(ENTITY_TABLES))
    entity_id = integer(data, 'entity_id')
    values = data.get('values')
    if not isinstance(values, dict):
        raise HTTPException(422, 'Send field values as an object.')
    with transaction() as conn:
        authorize(conn, user, company_id, ('manager', 'property'))
        linked(conn, ENTITY_TABLES[entity], entity_id, company_id)
        for raw_id, raw in values.items():
            f = conn.execute('SELECT * FROM custom_fields WHERE id=? AND company_id=? AND entity=?', (int(raw_id), company_id, entity)).fetchone()
            if not f:
                raise HTTPException(422, 'Unknown custom field.')
            value = str(raw if raw is not None else '').strip()[:500]
            if value:
                try:
                    if f['field_type'] == 'number':
                        if not Decimal(value).is_finite():
                            raise InvalidOperation()
                    elif f['field_type'] == 'date':
                        date_field({'d': value}, 'd')
                except (InvalidOperation, HTTPException):
                    raise HTTPException(422, f'{f["label"]} must be a valid {f["field_type"]}.')
                conn.execute('INSERT INTO custom_values(company_id,field_id,entity_id,value) VALUES(?,?,?,?) ON CONFLICT(field_id,entity_id) DO UPDATE SET value=excluded.value', (company_id, f['id'], entity_id, value))
            else:
                conn.execute('DELETE FROM custom_values WHERE field_id=? AND entity_id=?', (f['id'], entity_id))
        audit(conn, user['id'], company_id, 'custom_data_updated', entity, entity_id, {})
    return {'ok': True}
