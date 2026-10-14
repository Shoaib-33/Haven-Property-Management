"""Send a tenancy agreement for signature inside Haven.

Flow: staff send an offer (the agreement is rendered from a Lease template, can be edited for this tenant, and is then frozen),
the tenant reads it, fills in the form the landlord asked for and signs by drawing, typing or uploading a signature, staff review
the signed offer and approve it with their own signature. Approval creates the tenancy, so the tenant "joins" only after the
landlord has accepted the signature. Staff can return it for changes or withdraw it, and the tenant can decline.
"""
import base64
import hashlib
import io
import json
import re
import struct
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse

from . import db
from .db import audit, now, transaction
from .main import date_field, euros, insert, integer, money, save_document, task, text_field
from .portal_access import notify, notify_staff, share_document
from .security import authorize, current_user, linked, record
from .agreement_pdf import agreement_pdf_bytes
from .templates_docs import PLACEHOLDER, pdf_bytes, render

router = APIRouter()
STAFF_SEND = ('property', 'manager')
OPEN = ('sent', 'signed', 'returned')
MAX_SIGNATURE = 300 * 1024
MODES = ('draw', 'type', 'upload')
# What the landlord can ask the tenant to fill in: key -> (label, type, options)
PRESETS = {
    'phone': ('Phone number', 'text', None),
    'date_of_birth': ('Date of birth', 'date', None),
    'id_type': ('ID document type', 'select', ['Passport', 'ID card', 'Driving licence', 'Residence permit']),
    'id_number': ('ID document number', 'text', None),
    'current_address': ('Current address', 'textarea', None),
    'employer': ('Employer', 'text', None),
    'monthly_income': ('Net monthly income (EUR)', 'number', None),
    'emergency_name': ('Emergency contact name', 'text', None),
    'emergency_phone': ('Emergency contact phone', 'text', None),
    'house_rules': ('I have read and accept the house rules', 'checkbox', None),
    'privacy': ('I agree that my details are processed for this tenancy', 'checkbox', None),
}
MAX_FIELDS = 14


def build_form(raw):
    """Turn the landlord's choice (preset keys and a few custom questions) into the stored field definitions."""
    if raw is None:
        return None
    if not isinstance(raw, list) or len(raw) > MAX_FIELDS:
        raise HTTPException(422, f'Ask for at most {MAX_FIELDS} details.')
    out, custom = [], 0
    for item in raw:
        item = item if isinstance(item, dict) else {}
        required = item.get('required') is not False
        key = str(item.get('key', ''))
        if key in PRESETS:
            label, kind, options = PRESETS[key]
            if any(f['key'] == key for f in out):
                continue
            field = {'key': key, 'label': label, 'type': kind, 'required': required}
            if options:
                field['options'] = options
            out.append(field)
        elif item.get('label'):
            custom += 1
            label = str(item['label']).strip()[:80]
            if not label:
                continue
            out.append({'key': f'custom_{custom}', 'label': label, 'type': 'text', 'required': required})
    return out


def clean_form(defs, values):
    """Validate the tenant's answers against what the landlord asked for. Returns {key: text}."""
    values = values if isinstance(values, dict) else {}
    out = {}
    for f in defs or []:
        raw = values.get(f['key'])
        kind = f['type']
        if kind == 'checkbox':
            if raw is True:
                out[f['key']] = 'Yes'
            elif f['required']:
                raise HTTPException(422, f'Please tick: {f["label"]}')
            continue
        text = str(raw if raw is not None else '').strip()
        if not text:
            if f['required']:
                raise HTTPException(422, f'Please fill in: {f["label"]}')
            continue
        if kind == 'date':
            try:
                date.fromisoformat(text)
            except ValueError:
                raise HTTPException(422, f'{f["label"]} must be a valid date.')
        elif kind == 'number':
            try:
                number = Decimal(text)
                if not number.is_finite() or number < 0 or number > 1000000000:
                    raise ValueError()
            except (InvalidOperation, ValueError):
                raise HTTPException(422, f'{f["label"]} must be a valid amount.')
        elif kind == 'select':
            if text not in f.get('options', []):
                raise HTTPException(422, f'Choose a value for {f["label"]}.')
        out[f['key']] = text[:600 if kind == 'textarea' else 300]
    return out


def read_signature(data_url):
    """Accept a PNG data URL from the signature pad, check that it really is a small, non-empty PNG, and return its bytes."""
    prefix = 'data:image/png;base64,'
    if not isinstance(data_url, str) or not data_url.startswith(prefix) or len(data_url) > MAX_SIGNATURE * 2:
        raise HTTPException(422, 'Draw, type or upload your signature first.')
    try:
        raw = base64.b64decode(data_url[len(prefix):], validate=True)
    except ValueError:
        raise HTTPException(422, 'The signature image is not valid.')
    if len(raw) > MAX_SIGNATURE or not raw.startswith(b'\x89PNG\r\n\x1a\n') or len(raw) < 100:
        raise HTTPException(422, 'The signature image is not valid or too large.')
    width, height = struct.unpack('>II', raw[16:24])
    if not (20 <= width <= 2400 and 10 <= height <= 1200):
        raise HTTPException(422, 'The signature image has an unusual size.')
    try:
        from PIL import Image
        image = Image.open(io.BytesIO(raw)).convert('RGBA')
        alpha = image.getchannel('A')
        if alpha.getbbox() is None or (alpha.getextrema()[0] == 255 and image.convert('L').getextrema()[0] > 245):
            raise HTTPException(422, 'The signature is empty.')
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(422, 'The signature image could not be read.')
    return raw


BLANK_SIGN_LINE = re.compile(r'^\s*Signed (for the landlord|by the tenant):.*$', re.I | re.M)


def agreement_file(conn, offer, offer_id, stamp=''):
    """The agreement as a PDF: the (edited) contract text, the tenant's details and the signature block with whoever has signed so far."""
    from .invoice_tools import logo_bytes
    company = dict(conn.execute('SELECT name,address,kvk FROM companies WHERE id=?', (offer['company_id'],)).fetchone())
    defs = json.loads(offer['form_fields']) if offer['form_fields'] else []
    values = json.loads(offer['form_values']) if offer['form_values'] else {}
    text = BLANK_SIGN_LINE.sub('', offer['body']).rstrip() + details_text(defs, values) + stamp
    landlord = None
    if offer['landlord_signer_name'] or offer['landlord_signature']:
        landlord = {'label': 'Verhuurder / Landlord', 'image': bytes(offer['landlord_signature']) if offer['landlord_signature'] else None,
                    'caption': '\n'.join(x for x in (offer['landlord_signer_name'], offer['landlord_signer_title'], f'Ondertekend {offer["landlord_signed_at"][:10]}' if offer['landlord_signed_at'] else '') if x)}
    landlord = landlord or {'label': 'Verhuurder / Landlord', 'image': None, 'caption': 'Nog te ondertekenen / To be signed'}
    if offer['signer_name'] and offer['signed_at']:
        tenant = {'label': 'Huurder / Tenant', 'image': bytes(offer['tenant_signature']) if offer['tenant_signature'] else None,
                  'caption': f'{offer["signer_name"]}\nOndertekend {offer["signed_at"][:10]} {offer["signed_at"][11:19]} UTC'}
    else:
        tenant = {'label': 'Huurder / Tenant', 'image': None, 'caption': 'Te ondertekenen door de huurder / To be signed by the tenant'}
    return agreement_pdf_bytes(text, company=company, logo=logo_bytes(offer['company_id']), reference=f'HVO-{offer["company_id"]}-{offer_id:04d}', signatures=[landlord, tenant], subject=f'Huurovereenkomst {offer_id}')


def signature_url(raw):
    return 'data:image/png;base64,' + base64.b64encode(bytes(raw)).decode() if raw else None


def details_text(defs, values):
    lines = [f'- {f["label"]}: {values.get(f["key"], "-")}' for f in defs or [] if f['key'] in values]
    return ('\n\n## Bijlage: gegevens huurder | Appendix: tenant details\n' + '\n'.join(lines)) if lines else ''


def tenant_users(conn, company_id, tenant_id):
    return [r[0] for r in conn.execute('SELECT user_id FROM portal_accounts WHERE tenant_id=? AND company_id=? AND enabled=1', (tenant_id, company_id))]


def offer_context(conn, company_id, tenant, prop, user, start, end, basic, service, deposit):
    company = conn.execute('SELECT * FROM companies WHERE id=?', (company_id,)).fetchone()
    ctx = {'today': date.today().isoformat(), 'company.name': company['name'], 'company.address': company['address'], 'company.kvk': company['kvk'], 'user.name': user['name'],
           'tenant.name': tenant['name'], 'tenant.email': tenant['email'], 'tenant.phone': tenant['phone'],
           'property.name': prop['name'], 'property.address': prop['address'], 'property.city': prop['city'], 'property.postcode': prop['postcode'],
           'property.area': f'{prop["area"]:g}', 'property.rooms': str(prop['rooms']), 'property.energy_label': prop['energy_label'],
           'contract.start_date': start, 'contract.end_date': end or 'open-ended', 'contract.signed_on': 'the date of the last signature below', 'contract.deposit': euros(deposit),
           'rent.basic': euros(basic), 'rent.service': euros(service), 'rent.total': euros(basic + service),
           'company.payment_instructions': (company['payment_instructions'] or '').strip(),
           'contract.term': f'bepaalde tijd, eindigend op {end}' if end else 'onbepaalde tijd',
           'contract.term_en': f'a fixed term ending on {end}' if end else 'an indefinite period'}
    owner = conn.execute('SELECT o.name FROM owners o JOIN portfolios p ON p.owner_id=o.id WHERE p.id=?', (prop['portfolio_id'],)).fetchone() if prop['portfolio_id'] else None
    if owner:
        ctx['owner.name'] = owner['name']
    entity_ids = {'tenant': tenant['id'], 'property': prop['id']}
    for f in conn.execute('SELECT id,entity,key FROM custom_fields WHERE company_id=?', (company_id,)):
        if f['entity'] in entity_ids:
            row = conn.execute('SELECT value FROM custom_values WHERE field_id=? AND entity_id=?', (f['id'], entity_ids[f['entity']])).fetchone()
            if row and row['value'] != '':
                ctx[f'{f["entity"]}.{f["key"]}'] = row['value']
    return ctx


def overlaps(conn, prop_id, start, end, ignore_offer=None):
    contract = conn.execute("SELECT id FROM contracts WHERE property_id=? AND status IN ('active','notice given','pending signature') AND start_date<=? AND (end_date IS NULL OR end_date>=?)", (prop_id, end or '9999-12-31', start)).fetchone()
    if contract:
        return 'This unit already has a tenancy during those dates.'
    other = conn.execute("SELECT id FROM lease_offers WHERE property_id=? AND status IN ('sent','signed','returned') AND id<>? AND start_date<=? AND (end_date IS NULL OR end_date>=?)", (prop_id, ignore_offer or 0, end or '9999-12-31', start)).fetchone()
    return 'Another contract for this unit is already out for signature in those dates.' if other else None


def offer_inputs(data):
    company_id = integer(data, 'company_id')
    start = date_field(data, 'start_date')
    end = date_field(data, 'end_date') if data.get('end_date') else None
    if end and end < start:
        raise HTTPException(422, 'The end date must be on or after the start date.')
    return company_id, start, end, money(data, 'basic_rent'), money(data, 'service_charge', True), money(data, 'deposit', True)


def prepare(conn, user, data):
    company_id, start, end, basic, service, deposit = offer_inputs(data)
    authorize(conn, user, company_id, STAFF_SEND)
    tenant = linked(conn, 'tenants', integer(data, 'tenant_id'), company_id)
    prop = linked(conn, 'properties', integer(data, 'property_id'), company_id)
    template = linked(conn, 'doc_templates', integer(data, 'template_id'), company_id)
    if template['kind'] != 'Lease' or template['archived']:
        raise HTTPException(422, 'Choose an active Lease template.')
    if tenant['archived_at']:
        raise HTTPException(422, 'This tenant has been removed.')
    problem = overlaps(conn, prop['id'], start, end)
    if problem:
        raise HTTPException(409, problem)
    body, missing = render(template['body'], offer_context(conn, company_id, tenant, prop, user, start, end, basic, service, deposit))
    return {'company_id': company_id, 'start': start, 'end': end, 'basic': basic, 'service': service, 'deposit': deposit, 'tenant': tenant, 'prop': prop, 'template': template, 'body': body, 'missing': missing}


@router.post('/api/lease-offers/preview')
def preview_offer(data: dict, user=Depends(current_user)):
    """The contract text as it would be sent, so the landlord can read and edit it for this tenant first."""
    with transaction() as conn:
        ctx = prepare(conn, user, data)
    return {'body': ctx['body'], 'missing': ctx['missing']}


@router.post('/api/lease-offers')
def send_offer(data: dict, user=Depends(current_user)):
    form = build_form(data.get('form_fields'))
    with transaction() as conn:
        ctx = prepare(conn, user, data)
        company_id, start, end, basic, service, deposit = (ctx[k] for k in ('company_id', 'start', 'end', 'basic', 'service', 'deposit'))
        tenant, prop, template, body = ctx['tenant'], ctx['prop'], ctx['template'], ctx['body']
        users = tenant_users(conn, company_id, tenant['id'])
        if not users:
            raise HTTPException(422, f'{tenant["name"]} has no portal login yet. Invite them to the tenant portal first (Tenants > Tenant directory), then send the contract.')
        edited = 0
        custom = data.get('body')
        if isinstance(custom, str) and custom.strip():
            custom = custom.strip()
            if len(custom) < 80 or len(custom) > 60000:
                raise HTTPException(422, 'The contract text is too short or too long.')
            edited = 0 if custom == body.strip() else 1
            body = custom
        elif ctx['missing']:
            raise HTTPException(422, 'The template needs these fields first: ' + ', '.join(ctx['missing']) + '. Add them under Templates & fields.')
        if '{{' in body:
            raise HTTPException(422, 'The contract still contains an unfilled {{field}}. Fill it in or remove it.')
        sig = conn.execute('SELECT * FROM company_signatures WHERE company_id=?', (company_id,)).fetchone()
        offer_id = insert(conn, 'lease_offers', {'company_id': company_id, 'property_id': prop['id'], 'tenant_id': tenant['id'], 'template_id': template['id'], 'start_date': start, 'end_date': end,
                                                 'basic_cents': basic, 'service_cents': service, 'deposit_cents': deposit, 'body': body, 'status': 'sent', 'sent_by': user['id'], 'sent_at': now(),
                                                 'form_fields': json.dumps(form) if form is not None else None, 'edited': edited,
                                                 'landlord_signature': bytes(sig['data']) if sig else None, 'landlord_signature_mode': sig['mode'] if sig else None,
                                                 'landlord_signer_name': sig['signer_name'] if sig else None, 'landlord_signer_title': sig['signer_title'] if sig else None,
                                                 'landlord_signed_at': datetime.now(timezone.utc).isoformat(timespec='seconds') if sig else None})
        pdf = agreement_file(conn, dict(conn.execute('SELECT * FROM lease_offers WHERE id=?', (offer_id,)).fetchone()), offer_id)
        filename = re.sub(r'[^A-Za-z0-9._-]+', '-', f'Huurovereenkomst-{tenant["name"]}-{start}').strip('-') + '.pdf'
        doc_id = save_document(conn, company_id, None, user['id'], 'Lease draft', (pdf, 'application/pdf', '.pdf', filename))
        conn.execute('UPDATE lease_offers SET document_id=? WHERE id=?', (doc_id, offer_id))
        notify(conn, company_id, users, 'Contract ready to sign', f'{prop["name"]}: please read and sign your tenancy agreement.', 'documents')
        audit(conn, user['id'], company_id, 'sent', 'lease_offer', offer_id, {'tenant_id': tenant['id'], 'property_id': prop['id'], 'start_date': start, 'document_id': doc_id, 'landlord_signed': bool(sig), 'edited_text': bool(edited), 'asks': [f['key'] for f in form or []]})
    return {'id': offer_id, 'status': 'sent'}


def staff_offer(conn, offer_id, user, roles):
    return record(conn, 'lease_offers', offer_id, user, roles)


@router.post('/api/lease-offers/{offer_id}/withdraw')
def withdraw(offer_id: int, data: dict, user=Depends(current_user)):
    with transaction() as conn:
        offer = staff_offer(conn, offer_id, user, STAFF_SEND)
        if offer['status'] not in OPEN:
            raise HTTPException(409, 'This contract is already closed.')
        conn.execute("UPDATE lease_offers SET status='withdrawn',reviewed_by=?,reviewed_at=?,response_note=? WHERE id=?", (user['id'], now(), str(data.get('note', '')).strip()[:500], offer_id))
        notify(conn, offer['company_id'], tenant_users(conn, offer['company_id'], offer['tenant_id']), 'Contract withdrawn', 'The landlord withdrew the contract you were asked to sign.', 'documents')
        audit(conn, user['id'], offer['company_id'], 'withdrawn', 'lease_offer', offer_id, {})
    return {'ok': True}


@router.post('/api/lease-offers/{offer_id}/return')
def send_back(offer_id: int, data: dict, user=Depends(current_user)):
    note = text_field(data, 'note', maximum=500)
    with transaction() as conn:
        offer = staff_offer(conn, offer_id, user, STAFF_SEND)
        if offer['status'] != 'signed':
            raise HTTPException(409, 'Only a signed contract can be returned.')
        conn.execute("UPDATE lease_offers SET status='returned',reviewed_by=?,reviewed_at=?,response_note=?,signer_name=NULL,signed_at=NULL,signed_ip=NULL,signed_hash=NULL,tenant_signature=NULL,tenant_signature_mode=NULL WHERE id=?", (user['id'], now(), note, offer_id))
        notify(conn, offer['company_id'], tenant_users(conn, offer['company_id'], offer['tenant_id']), 'Contract returned to you', note, 'documents')
        audit(conn, user['id'], offer['company_id'], 'returned', 'lease_offer', offer_id, {'note': note})
    return {'ok': True}


@router.post('/api/lease-offers/{offer_id}/approve')
def approve(offer_id: int, data: dict, user=Depends(current_user)):
    if data.get('confirmed') is not True:
        raise HTTPException(422, 'Confirm that you checked the signed contract.')
    with transaction() as conn:
        offer = staff_offer(conn, offer_id, user, STAFF_SEND)
        if offer['status'] != 'signed':
            raise HTTPException(409, 'The tenant has not signed this contract yet.')
        problem = overlaps(conn, offer['property_id'], offer['start_date'], offer['end_date'], offer_id)
        if problem:
            raise HTTPException(409, problem)
        tenant = linked(conn, 'tenants', offer['tenant_id'], offer['company_id'])
        signed_day = offer['signed_at'][:10]
        contract_id = insert(conn, 'contracts', {'company_id': offer['company_id'], 'property_id': offer['property_id'], 'tenant_id': offer['tenant_id'], 'signed_on': signed_day,
                                                 'start_date': offer['start_date'], 'end_date': offer['end_date'], 'status': 'active', 'deposit_cents': offer['deposit_cents'], 'created_at': now()})
        insert(conn, 'rent_versions', {'company_id': offer['company_id'], 'contract_id': contract_id, 'effective_from': offer['start_date'], 'basic_cents': offer['basic_cents'], 'service_cents': offer['service_cents'], 'created_at': now(), 'created_by': user['id']})
        defs = json.loads(offer['form_fields']) if offer['form_fields'] else []
        values = json.loads(offer['form_values']) if offer['form_values'] else {}
        landlord_sig, landlord_mode = (bytes(offer['landlord_signature']), offer['landlord_signature_mode']) if offer['landlord_signature'] else (None, None)
        if data.get('signature'):   # a signature given while approving replaces the one from sending
            landlord_sig = read_signature(data['signature'])
            landlord_mode = data.get('signature_mode') if data.get('signature_mode') in MODES else 'draw'
        signed_for = dict(offer, landlord_signature=landlord_sig, landlord_signer_name=offer['landlord_signer_name'] or user['name'], landlord_signer_title=offer['landlord_signer_title'] or '',
                          landlord_signed_at=offer['landlord_signed_at'] or datetime.now(timezone.utc).isoformat(timespec='seconds'))
        stamp = (f'\n\n## Elektronische ondertekening | Electronic signature record\nSigned by the tenant: {offer["signer_name"]} on {offer["signed_at"][:10]} at {offer["signed_at"][11:19]} UTC (IP {offer["signed_ip"]}).\n'
                 f'Signed for the landlord: {signed_for["landlord_signer_name"]} on {signed_for["landlord_signed_at"][:10]}. Accepted by {user["name"]} on {date.today().isoformat()}.\nDocument fingerprint (SHA-256 of the agreement text and the tenant details): {offer["signed_hash"]}')
        pdf = agreement_file(conn, signed_for, offer_id, stamp)
        filename = re.sub(r'[^A-Za-z0-9._-]+', '-', f'Getekende-huurovereenkomst-{tenant["name"]}-{offer["start_date"]}').strip('-') + '.pdf'
        final_id = save_document(conn, offer['company_id'], contract_id, user['id'], 'Contract', (pdf, 'application/pdf', '.pdf', filename))
        share_document(conn, offer['company_id'], contract_id, final_id, user['id'])
        conn.execute("UPDATE lease_offers SET status='approved',reviewed_by=?,reviewed_at=?,contract_id=?,final_document_id=?,landlord_signature=?,landlord_signature_mode=?,landlord_signer_name=?,landlord_signer_title=?,landlord_signed_at=? WHERE id=?",
                     (user['id'], now(), contract_id, final_id, landlord_sig, landlord_mode, signed_for['landlord_signer_name'], signed_for['landlord_signer_title'], signed_for['landlord_signed_at'], offer_id))
        task(conn, offer['company_id'], contract_id, 'Complete move-in inspection', offer['start_date'], 'Inspection')
        task(conn, offer['company_id'], contract_id, 'Collect deposit and confirm WWS points', offer['start_date'], 'Contract')
        notify(conn, offer['company_id'], tenant_users(conn, offer['company_id'], offer['tenant_id']), 'Welcome home: tenancy confirmed', 'Your signed agreement has been accepted. You can find it in Documents.', 'documents')
        audit(conn, user['id'], offer['company_id'], 'approved', 'lease_offer', offer_id, {'contract_id': contract_id, 'document_id': final_id})
    return {'contract_id': contract_id}


@router.get('/api/lease-offers/{offer_id}/review')
def review(offer_id: int, user=Depends(current_user)):
    with transaction() as conn:
        offer = staff_offer(conn, offer_id, user, STAFF_SEND)
        audit(conn, user['id'], offer['company_id'], 'reviewed', 'lease_offer', offer_id, {})
    defs = json.loads(offer['form_fields']) if offer['form_fields'] else []
    values = json.loads(offer['form_values']) if offer['form_values'] else {}
    return {'id': offer_id, 'status': offer['status'], 'edited': bool(offer['edited']), 'fields': defs, 'values': values, 'signer_name': offer['signer_name'], 'signed_at': offer['signed_at'], 'signed_ip': offer['signed_ip'],
            'signature': signature_url(offer['tenant_signature']), 'signature_mode': offer['tenant_signature_mode'], 'fingerprint': offer['signed_hash'],
            'landlord_signed': bool(offer['landlord_signature']), 'landlord_name': offer['landlord_signer_name']}


def send_file(conn, offer, download=False):
    """Sent or returned: the draft. Signed by the tenant: the contract with the tenant's details and signature, waiting for the landlord.
    Approved: the final signed agreement with both signatures."""
    from fastapi.responses import Response
    if offer['status'] == 'signed' and offer.get('signer_name'):
        return Response(agreement_file(conn, offer, offer['id']), media_type='application/pdf', headers={'Content-Disposition': 'inline; filename="Huurovereenkomst-getekend-door-huurder.pdf"'})
    doc_id = offer['final_document_id'] if offer['status'] == 'approved' and offer.get('final_document_id') else offer['document_id']
    doc = conn.execute('SELECT * FROM documents WHERE id=?', (doc_id,)).fetchone()
    return FileResponse(db.DATA_DIR / 'uploads' / doc['file_key'], media_type=doc['mime'], filename=doc['name'], content_disposition_type='attachment' if download else 'inline')


@router.get('/api/lease-offers/{offer_id}/file')
def staff_file(offer_id: int, download: int = 0, user=Depends(current_user)):
    with transaction() as conn:
        offer = staff_offer(conn, offer_id, user, None)
        audit(conn, user['id'], offer['company_id'], 'downloaded' if download else 'viewed', 'lease_offer', offer_id, {})
        return send_file(conn, offer, bool(download))


# ---------- tenant side ----------

def own_offer(conn, offer_id, user):
    row = conn.execute('SELECT o.* FROM lease_offers o JOIN portal_accounts a ON a.tenant_id=o.tenant_id AND a.company_id=o.company_id WHERE o.id=? AND a.user_id=? AND a.enabled=1', (offer_id, user['id'])).fetchone()
    if not row:
        raise HTTPException(404, 'Contract not found.')
    return dict(row)


def tenant_offers(conn, user_id):
    sql = '''SELECT o.id,o.status,o.start_date,o.end_date,o.basic_cents,o.service_cents,o.deposit_cents,o.sent_at,o.signed_at,o.signer_name,o.response_note,o.final_document_id,o.body,o.form_fields,o.form_values,
             p.name property_name,p.address,p.city,t.name tenant_name,co.name company_name
             FROM lease_offers o JOIN portal_accounts a ON a.tenant_id=o.tenant_id AND a.company_id=o.company_id
             JOIN properties p ON p.id=o.property_id JOIN tenants t ON t.id=o.tenant_id JOIN companies co ON co.id=o.company_id
             WHERE a.user_id=? AND a.enabled=1 AND o.status<>'withdrawn' ORDER BY o.id DESC'''
    out = []
    for r in conn.execute(sql, (user_id,)):
        item = dict(r)
        item['form_fields'] = json.loads(item['form_fields']) if item['form_fields'] else None
        item['form_values'] = json.loads(item['form_values']) if item['form_values'] else {}
        if item['status'] in ('approved', 'declined', 'signed'):
            item['body'] = None  # the signed contract is read as the PDF
        out.append(item)
    return out


def norm(name):
    return re.sub(r'\s+', ' ', str(name or '')).strip().casefold()


@router.get('/api/tenant/lease-offers/{offer_id}/file')
def tenant_file(offer_id: int, download: int = 0, user=Depends(current_user)):
    with transaction() as conn:
        offer = own_offer(conn, offer_id, user)
        audit(conn, user['id'], offer['company_id'], 'viewed', 'lease_offer', offer_id, {})
        return send_file(conn, offer, bool(download))


@router.post('/api/tenant/lease-offers/{offer_id}/sign')
def sign(offer_id: int, data: dict, request: Request, user=Depends(current_user)):
    if data.get('agree') is not True:
        raise HTTPException(422, 'Tick the box to confirm you have read and agree to the contract.')
    with transaction() as conn:
        offer = own_offer(conn, offer_id, user)
        if offer['status'] not in ('sent', 'returned'):
            raise HTTPException(409, 'This contract cannot be signed any more.')
        tenant = conn.execute('SELECT * FROM tenants WHERE id=?', (offer['tenant_id'],)).fetchone()
        defs = json.loads(offer['form_fields']) if offer['form_fields'] is not None else None
        values = clean_form(defs, data.get('form'))
        image, mode = None, None
        if defs is not None or data.get('signature'):
            image = read_signature(data.get('signature'))
            mode = data.get('signature_mode') if data.get('signature_mode') in MODES else 'draw'
        elif norm(data.get('signer_name')) != norm(tenant['name']):
            # older contracts (no signature pad): the typed full name is the signature
            raise HTTPException(422, f'Type your full name exactly as it appears here: {tenant["name"]}')
        stamp = datetime.now(timezone.utc).isoformat(timespec='seconds')
        ip = request.client.host if request.client else 'unknown'
        digest = hashlib.sha256((offer['body'] + json.dumps(values, sort_keys=True)).encode()).hexdigest() if values else hashlib.sha256(offer['body'].encode()).hexdigest()
        conn.execute("UPDATE lease_offers SET status='signed',signer_name=?,signed_at=?,signed_ip=?,signed_hash=?,response_note=NULL,form_values=?,tenant_signature=?,tenant_signature_mode=? WHERE id=?",
                     (tenant['name'], stamp, ip, digest, json.dumps(values) if defs is not None else None, image, mode, offer_id))
        if values.get('phone') and not (tenant['phone'] or '').strip():
            conn.execute('UPDATE tenants SET phone=? WHERE id=?', (values['phone'], tenant['id']))
        notify_staff(conn, offer['company_id'], 'Contract signed, approval needed', f'{tenant["name"]} signed the tenancy agreement.', 'tenants')
        audit(conn, user['id'], offer['company_id'], 'signed', 'lease_offer', offer_id, {'ip': ip, 'sha256': digest, 'signature_mode': mode, 'details_given': sorted(values)})
    return {'ok': True}


@router.post('/api/tenant/lease-offers/{offer_id}/decline')
def decline(offer_id: int, data: dict, user=Depends(current_user)):
    reason = text_field(data, 'reason', maximum=500)
    with transaction() as conn:
        offer = own_offer(conn, offer_id, user)
        if offer['status'] not in ('sent', 'returned', 'signed'):
            raise HTTPException(409, 'This contract is already closed.')
        conn.execute("UPDATE lease_offers SET status='declined',response_note=? WHERE id=?", (reason, offer_id))
        notify_staff(conn, offer['company_id'], 'Contract declined by tenant', reason, 'tenants')
        audit(conn, user['id'], offer['company_id'], 'declined', 'lease_offer', offer_id, {'reason': reason})
    return {'ok': True}


# ---------- the landlord's own signature, kept once per company and placed on every contract at sending ----------

@router.get('/api/companies/{company_id}/signature')
def get_signature(company_id: int, user=Depends(current_user)):
    with db.connect() as conn:
        authorize(conn, user, company_id, STAFF_SEND)
        row = conn.execute('SELECT * FROM company_signatures WHERE company_id=?', (company_id,)).fetchone()
    if not row:
        return {'set': False}
    return {'set': True, 'name': row['signer_name'], 'title': row['signer_title'], 'mode': row['mode'], 'signature': signature_url(row['data']), 'updated_at': row['updated_at']}


@router.post('/api/companies/{company_id}/signature')
def set_signature(company_id: int, data: dict, user=Depends(current_user)):
    name = text_field(data, 'name', maximum=100)
    title = str(data.get('title') or '').strip()[:100]
    image = read_signature(data.get('signature'))
    mode = data.get('signature_mode') if data.get('signature_mode') in MODES else 'draw'
    with transaction() as conn:
        authorize(conn, user, company_id, ('manager',))
        conn.execute('INSERT INTO company_signatures(company_id,signer_name,signer_title,mode,data,updated_at) VALUES(?,?,?,?,?,?) ON CONFLICT(company_id) DO UPDATE SET '
                     'signer_name=excluded.signer_name,signer_title=excluded.signer_title,mode=excluded.mode,data=excluded.data,updated_at=excluded.updated_at', (company_id, name, title, mode, image, now()))
        audit(conn, user['id'], company_id, 'signature_set', 'company', company_id, {'signer': name, 'mode': mode})
    return {'ok': True}


@router.delete('/api/companies/{company_id}/signature')
def delete_signature(company_id: int, user=Depends(current_user)):
    with transaction() as conn:
        authorize(conn, user, company_id, ('manager',))
        conn.execute('DELETE FROM company_signatures WHERE company_id=?', (company_id,))
        audit(conn, user['id'], company_id, 'signature_removed', 'company', company_id, {})
    return {'ok': True}
