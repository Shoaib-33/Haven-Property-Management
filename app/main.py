import calendar
import asyncio
import csv
import hashlib
import io
import json
import logging
import os
import secrets
import sqlite3
import time
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .clock import local_today
from . import db, sla
from .cbs import CBSDataError, cpi_annual_rate
from .db import audit, connect, now, transaction
from .security import PORTALS, authorize, current_user, session_cookie, expiry, linked, needs_rehash, password_hash, record, token_hash, verify_password

ROOT = Path(__file__).resolve().parent.parent
from .config import load_local_env
load_local_env()
FINANCE = ('finance', 'manager')
OPERATIONS = ('property', 'manager')


@asynccontextmanager
async def lifespan(app):
    db.init_db()
    billing_task = asyncio.create_task(monthly_billing_scheduler())
    app.state.billing_task = billing_task
    yield
    billing_task.cancel()
    try:
        await billing_task
    except asyncio.CancelledError:
        pass


app = FastAPI(title='Haven property management', version='1.0.0', lifespan=lifespan)


@app.middleware('http')
async def security_headers(request, call_next):
    # Same-origin requests only, including public receipt uploads and login.
    if request.method not in ('GET', 'HEAD', 'OPTIONS'):
        origin = request.headers.get('origin')
        expected = str(request.base_url).rstrip('/')
        if origin and origin != expected:
            return JSONResponse({'detail': 'Cross-origin writes are not allowed.'}, status_code=403)
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    response.headers['Cache-Control'] = 'no-store'
    if response.headers.get('content-type', '').startswith(('application/pdf', 'image/png', 'image/jpeg')):
        # only Haven's own pages may show a PDF or a receipt photo inside the portal viewer
        response.headers['X-Frame-Options'] = 'SAMEORIGIN'
        response.headers['Content-Security-Policy'] = response.headers['Content-Security-Policy'].replace("frame-ancestors 'none'", "frame-ancestors 'self'")
    return response


@app.exception_handler(sqlite3.IntegrityError)
async def integrity_error(request, exc):
    return JSONResponse({'detail': 'This operation conflicts with an existing or protected record.'}, status_code=409)


def text_field(data, key, default=None, maximum=5000):
    value = data.get(key, default)
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise HTTPException(422, f'{key.replace("_", " ").capitalize()} is required (maximum {maximum} characters).')
    return value.strip()


def integer(data, key, default=None):
    try:
        v = data.get(key, default)
        if isinstance(v, bool) or str(int(v)) != str(v):
            raise ValueError()
        return int(v)
    except (ValueError, TypeError):
        raise HTTPException(422, f'{key} must be a whole number.')


def money(data, key, allow_zero=False):
    try:
        value = Decimal(str(data.get(key, '')))
        if not value.is_finite() or value < 0 or (value == 0 and not allow_zero) or value > 10000000:
            raise ValueError()
        cents = value * 100
        if cents != cents.to_integral_value():
            raise ValueError()
        return int(cents)
    except (InvalidOperation, ValueError):
        raise HTTPException(422, f'{key} must be a valid euro amount with at most two decimal places.')


def date_field(data, key, default=None):
    try:
        raw = data.get(key, default)
        parsed = date.fromisoformat(raw)
        if parsed.isoformat() != raw:
            raise ValueError()
        return raw
    except (TypeError, ValueError):
        raise HTTPException(422, f'{key} must be a date in YYYY-MM-DD format.')


def choice(data, key, choices, default=None):
    value = data.get(key, default)
    if value not in choices:
        raise HTTPException(422, f'Invalid {key}.')
    return value


def insert(conn, table, values):
    columns = ','.join(values)
    cur = conn.execute(f'INSERT INTO {table} ({columns}) VALUES ({",".join("?" for _ in values)})', tuple(values.values()))
    return cur.lastrowid


def task(conn, company, contract, title, due, category, owner='Property team'):
    conn.execute('INSERT OR IGNORE INTO tasks(company_id,contract_id,title,due_date,owner,category,created_at) VALUES(?,?,?,?,?,?,?)', (company, contract, title, due, owner, category, now()))


def invoice_balance(conn, invoice_id):
    return conn.execute('SELECT COALESCE(SUM(amount_cents),0) FROM ledger WHERE invoice_id=?', (invoice_id,)).fetchone()[0]


def rent_at(conn, contract_id, when):
    return conn.execute('SELECT * FROM rent_versions WHERE contract_id=? AND effective_from<=? ORDER BY effective_from DESC LIMIT 1', (contract_id, when)).fetchone()


def issue_month(conn, company_id, period, user_id):
    try:
        first = date.fromisoformat(period + '-01')
        last = date(first.year, first.month, calendar.monthrange(first.year, first.month)[1])
    except (ValueError, TypeError):
        raise HTTPException(422, 'Period must be YYYY-MM.')
    review = bool(conn.execute('SELECT review_invoices FROM companies WHERE id=?', (company_id,)).fetchone()[0])
    contracts = conn.execute('SELECT c.*,t.name tenant_name,t.email tenant_email,p.address,p.name property_name,co.name company_name,co.address company_address,co.payment_instructions FROM contracts c JOIN tenants t ON t.id=c.tenant_id JOIN properties p ON p.id=c.property_id JOIN companies co ON co.id=c.company_id WHERE c.company_id=? AND c.status IN ("active","notice given") AND c.start_date<=? AND (c.end_date IS NULL OR c.end_date>=?)', (company_id, last.isoformat(), first.isoformat())).fetchall()
    count = 0
    for c in contracts:
        if conn.execute('SELECT id FROM invoices WHERE contract_id=? AND period=?', (c['id'], period)).fetchone():
            continue
        start = max(first, date.fromisoformat(c['start_date']))
        end = min(last, date.fromisoformat(c['end_date'])) if c['end_date'] else last
        basic = Decimal(0)
        service = Decimal(0)
        versions = conn.execute('SELECT * FROM rent_versions WHERE contract_id=? ORDER BY effective_from', (c['id'],)).fetchall()
        for offset in range((end - start).days + 1):
            day = (start + timedelta(days=offset)).isoformat()
            r = None
            for v in versions:
                if v['effective_from'] <= day:
                    r = v
            if not r:
                raise HTTPException(409, f'Contract #{c["id"]} has no rent history covering {day}.')
            basic += Decimal(r['basic_cents']) / last.day
            service += Decimal(r['service_cents']) / last.day
        b, s = int(basic.quantize(Decimal('1'), rounding=ROUND_HALF_UP)), int(service.quantize(Decimal('1'), rounding=ROUND_HALF_UP))
        number = f'HVN-{company_id}-{period.replace("-", "")}-{c["id"]:04d}'
        snapshot = {k: c[k] for k in ('tenant_name', 'tenant_email', 'address', 'property_name', 'company_name', 'company_address', 'payment_instructions')}
        snapshot.update({'from': start.isoformat(), 'to': end.isoformat(), 'basic_cents': b, 'service_cents': s})
        invoice_id = insert(conn, 'invoices', {'company_id': company_id, 'contract_id': c['id'], 'number': number, 'period': period, 'due_date': start.isoformat(), 'basic_cents': b, 'service_cents': s, 'total_cents': b+s, 'snapshot': json.dumps(snapshot), 'created_at': now()})
        insert(conn, 'ledger', {'company_id': company_id, 'contract_id': c['id'], 'invoice_id': invoice_id, 'kind': 'invoice', 'amount_cents': b+s, 'created_by': user_id, 'created_at': now(), 'note': number})
        audit(conn, user_id, company_id, 'issued', 'invoice', invoice_id, {'number': number, 'total_cents': b+s})
        if review:   # a draft: the landlord checks and edits it, then sends it to the tenant
            count += 1
            continue
        conn.execute('UPDATE invoices SET released_at=? WHERE id=?', (now(), invoice_id))
        from .email_delivery import queue_invoice
        queue_invoice(conn, {'id': invoice_id, 'company_id': company_id, 'contract_id': c['id']}, c['tenant_email'])
        from .portal_access import notify_staff, notify_tenant
        portal_ready = conn.execute('SELECT 1 FROM portal_accounts WHERE tenant_id=? AND company_id=? AND enabled=1', (c['tenant_id'], company_id)).fetchone()
        if portal_ready:
            notify_tenant(conn, c['id'], 'Monthly rent bill ready', f'Invoice {number} for {period} is available. Amount: {euros(b+s)}. Due: {start.isoformat()}.', 'payments')
        else:
            notify_staff(conn, company_id, 'Rent bill issued; tenant portal not active', f'{c["tenant_name"]} has a new invoice ({number}) but no active tenant portal account to receive the alert.', 'settings')
        count += 1
    if review and count:
        from .portal_access import notify_staff
        notify_staff(conn, company_id, 'Invoices ready for review', f'{count} draft invoice{"s" if count != 1 else ""} for {period}. Check them, edit if needed, then send them to the tenants.', 'invoices')
    return count


def purge_expired_sessions():
    with transaction() as conn:
        conn.execute('DELETE FROM sessions WHERE expires_at<=?', (now(),))
        conn.execute('DELETE FROM login_attempts WHERE blocked_until<?', (time.time(),))


def scheduled_invoice_period(today=None):
    today = today or local_today()
    try:
        run_day = int(os.environ.get('HAVEN_RENT_BILL_DAY', '15'))
    except ValueError:
        run_day = 15
    run_day = max(1, min(28, run_day))
    if today.day >= run_day:
        year, month = today.year, today.month + 1
        if month == 13:
            year, month = year + 1, 1
        return f'{year:04d}-{month:02d}'
    if today.day <= 3:
        return today.strftime('%Y-%m')
    return None


def run_scheduled_invoices(period):
    first = date.fromisoformat(period + '-01')
    last = date(first.year, first.month, calendar.monthrange(first.year, first.month)[1])
    with connect() as conn:
        companies = [r['company_id'] for r in conn.execute('SELECT DISTINCT company_id FROM contracts WHERE start_date<=? AND (end_date IS NULL OR end_date>=?)', (last.isoformat(), first.isoformat()))]
    for company_id in companies:
        try:
            with transaction() as conn:
                issue_month(conn, company_id, period, None)
        except Exception:
            logging.exception('Scheduled billing failed for company %s; other companies continue.', company_id)


def process_overdue_invoices(company_id=None):
    """Start and advance reminder tasks for unpaid invoices; legal steps remain staff controlled."""
    today=local_today()
    created=0
    with connect() as conn:
        companies=[company_id] if company_id else [r[0] for r in conn.execute('SELECT DISTINCT company_id FROM invoices WHERE due_date<?',(today.isoformat(),))]
    from .portal_access import notify_tenant
    from .email_delivery import queue_reminder
    for company in companies:
        with transaction() as conn:
            invoices=conn.execute('SELECT i.*,t.email tenant_email FROM invoices i JOIN contracts c ON c.id=i.contract_id JOIN tenants t ON t.id=c.tenant_id WHERE i.company_id=? AND i.due_date<? AND i.released_at IS NOT NULL',(company,today.isoformat())).fetchall()
            for inv in invoices:
                balance=invoice_balance(conn,inv['id'])
                case=conn.execute('SELECT * FROM arrears_cases WHERE invoice_id=?',(inv['id'],)).fetchone()
                if balance<=0:
                    if case and case['status'] in ('open','paused'):
                        conn.execute('UPDATE arrears_cases SET status="resolved",paused_until=NULL,updated_at=? WHERE id=?',(now(),case['id']))
                        insert(conn,'arrears_events',{'company_id':company,'case_id':case['id'],'step':'balance_cleared','occurred_on':today.isoformat(),'note':'Verified ledger balance is zero.','created_at':now()})
                    continue
                due=date.fromisoformat(inv['due_date'])
                age=(today-due).days
                if age<1:
                    continue
                if not case:
                    case_id=insert(conn,'arrears_cases',{'company_id':company,'contract_id':inv['contract_id'],'invoice_id':inv['id'],'status':'open','created_at':now(),'updated_at':now()})
                    created+=1
                    active=conn.execute('SELECT 1 FROM portal_accounts a JOIN contracts c ON c.tenant_id=a.tenant_id AND c.company_id=a.company_id WHERE c.id=? AND a.enabled=1',(inv['contract_id'],)).fetchone()
                    if active:
                        notify_tenant(conn,inv['contract_id'],'Rent payment reminder',f'Invoice {inv["number"]} still has an outstanding balance. Please pay using your agreed payment method, submit proof in Haven, or contact your property manager. Balance: {euros(balance)}.','payments')
                        insert(conn,'arrears_events',{'company_id':company,'case_id':case_id,'step':'friendly_reminder_sent','occurred_on':today.isoformat(),'note':'In-app reminder sent. External payment methods remain unchanged.','created_at':now()})
                    else:
                        task(conn,company,inv['contract_id'],f'Send friendly rent reminder for invoice {inv["number"]}',today.isoformat(),'Arrears','Finance team')
                    case=conn.execute('SELECT * FROM arrears_cases WHERE id=?',(case_id,)).fetchone()
                elif case['status']=='resolved':
                    conn.execute('UPDATE arrears_cases SET status="open",paused_until=NULL,updated_at=? WHERE id=?',(now(),case['id']))
                    insert(conn,'arrears_events',{'company_id':company,'case_id':case['id'],'step':'case_reopened','occurred_on':today.isoformat(),'note':'An unpaid balance returned after the case was resolved.','created_at':now()})
                    case=conn.execute('SELECT * FROM arrears_cases WHERE id=?',(case['id'],)).fetchone()
                if case['status']=='paused' and case['paused_until'] and case['paused_until']<=today.isoformat():
                    conn.execute('UPDATE arrears_cases SET status="open",paused_until=NULL,updated_at=? WHERE id=?',(now(),case['id']))
                    insert(conn,'arrears_events',{'company_id':company,'case_id':case['id'],'step':'follow_up_resumed','occurred_on':today.isoformat(),'note':'The 30-day support pause ended.','created_at':now()})
                elif case['status']=='paused':
                    continue
                if 1 <= age < 8:
                    queue_reminder(conn, inv, 'overdue_friendly', inv['tenant_email'])
                if age >= 8:
                    queue_reminder(conn, inv, 'overdue_followup', inv['tenant_email'])
                task(conn,company,inv['contract_id'],f'Call tenant about overdue invoice {inv["number"]}',(due+timedelta(days=7)).isoformat(),'Arrears','Finance team')
                if age>=8 and not conn.execute('SELECT 1 FROM arrears_events WHERE case_id=? AND step="written_reminder_sent"',(case['id'],)).fetchone():
                    task(conn,company,inv['contract_id'],f'Send written arrears reminder for invoice {inv["number"]}',(due+timedelta(days=8)).isoformat(),'Arrears','Finance team')
                if age>=30 and not conn.execute('SELECT 1 FROM arrears_events WHERE case_id=? AND step="municipality_reported"',(case['id'],)).fetchone():
                    task(conn,company,inv['contract_id'],f'Check early-warning eligibility for invoice {inv["number"]}',(due+timedelta(days=30)).isoformat(),'Arrears','Property manager')
                if age>=90 and not conn.execute('SELECT 1 FROM arrears_events WHERE case_id=? AND step="court_review_decision"',(case['id'],)).fetchone():
                    task(conn,company,inv['contract_id'],f'Manager review: 90-day arrears for invoice {inv["number"]}',(due+timedelta(days=90)).isoformat(),'Arrears','Property manager')
                conn.execute('UPDATE arrears_cases SET updated_at=? WHERE id=?',(now(),case['id']))
    return created


async def monthly_billing_scheduler():
    while True:
        from .email_delivery import deliver_pending_emails
        from .automation import run_all_companies
        steps = [(run_scheduled_invoices, (scheduled_invoice_period(),)), (process_overdue_invoices, ()), (run_all_companies, ()), (deliver_pending_emails, ()), (purge_expired_sessions, ())]
        for step, args in steps:
            try:
                if step is run_scheduled_invoices and not args[0]:
                    continue
                await asyncio.to_thread(step, *args)
            except asyncio.CancelledError:
                raise
            except Exception:
                logging.exception('Scheduled step %s failed; remaining steps still run.', step.__name__)
        await asyncio.sleep(6 * 60 * 60)


@app.get('/api/health')
def health():
    return {'status': 'ok', 'app': 'Haven', 'payment_mode': 'manual verification'}


@app.post('/api/login')
def login(data: dict, request: Request):
    email = text_field(data, 'email', maximum=254).lower()
    password = text_field(data, 'password', maximum=1024)
    attempt_key = hashlib.sha256((email + '|' + (request.client.host if request.client else 'local')).encode()).hexdigest()
    with transaction() as conn:
        attempt = conn.execute('SELECT * FROM login_attempts WHERE key=?', (attempt_key,)).fetchone()
        if attempt and attempt['attempts'] >= 8 and attempt['blocked_until'] > time.time():
            raise HTTPException(429, 'Too many attempts. Try again in 15 minutes.')
        user = conn.execute('SELECT * FROM users WHERE email=? AND active=1', (email,)).fetchone()
        valid = verify_password(password, user['password_hash']) if user else verify_password(password, 'pbkdf2:600000:' + 'a'*32 + ':' + '0'*64)
        if not valid:
            attempts = attempt['attempts'] + 1 if attempt and attempt['blocked_until'] > time.time() else 1
            conn.execute('INSERT OR REPLACE INTO login_attempts VALUES(?,?,?)', (attempt_key, attempts, time.time()+900))
        else:
            conn.execute('DELETE FROM login_attempts WHERE key=?', (attempt_key,))
            if needs_rehash(user['password_hash']):
                conn.execute('UPDATE users SET password_hash=? WHERE id=?', (password_hash(password), user['id']))
            token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
            conn.execute('INSERT INTO sessions VALUES(?,?,?,?)', (token_hash(token), user['id'], expiry(), csrf))
            audit(conn, user['id'], None, 'signed_in', 'user', user['id'], {})
    if not valid:
        raise HTTPException(401, 'Email or password is incorrect.')
    response = JSONResponse({'csrf': csrf, 'name': user['name']})
    cookie = 'haven_' + request.headers.get('X-Haven-Portal', '') + '_session' if request.headers.get('X-Haven-Portal', '') in PORTALS else 'haven_session'
    response.set_cookie(cookie, token, httponly=True, samesite='strict', secure=os.environ.get('HAVEN_SECURE_COOKIES') == '1', max_age=86400)
    return response


@app.post('/api/logout')
def logout(request: Request, user=Depends(current_user)):
    cookie = session_cookie(request)
    with transaction() as conn:
        conn.execute('DELETE FROM sessions WHERE token_hash=?', (token_hash(request.cookies.get(cookie, '')),))
    response = JSONResponse({'ok': True})
    response.delete_cookie(cookie)
    return response


@app.get('/api/me')
def me(user=Depends(current_user)):
    with connect() as conn:
        companies = [dict(r) for r in conn.execute('SELECT c.*,m.role,(SELECT updated_at FROM company_logos l WHERE l.company_id=c.id) logo_version FROM companies c JOIN memberships m ON m.company_id=c.id WHERE m.user_id=? ORDER BY c.name', (user['id'],))]
        tenant_access = bool(conn.execute('SELECT 1 FROM portal_accounts WHERE user_id=? AND enabled=1', (user['id'],)).fetchone())
        contractor_access = bool(conn.execute('SELECT 1 FROM contractor_accounts WHERE user_id=? AND enabled=1', (user['id'],)).fetchone())
        owner_access = bool(conn.execute('SELECT 1 FROM owner_accounts WHERE user_id=? AND enabled=1', (user['id'],)).fetchone())
    return {'id': user['id'], 'name': user['name'], 'email': user['email'], 'csrf': user['csrf'], 'global_admin': bool(user['global_admin']), 'companies': companies, 'tenant_access': tenant_access, 'contractor_access': contractor_access, 'owner_access': owner_access}


@app.get('/api/data')
def all_data(company_id: int, user=Depends(current_user)):
    with connect() as conn:
        role = authorize(conn, user, company_id)
        result = {'role': role, 'today': local_today().isoformat()}
        operational = ['properties', 'tenants', 'contracts', 'repairs', 'tasks', 'communications', 'inspections']
        financial = ['rent_versions', 'deposit_events', 'cost_lines', 'settlements', 'rent_proposals', 'valuations', 'rules']
        if role == 'inspector':
            assigned = [r['property_id'] for r in conn.execute('SELECT property_id FROM inspector_assignments WHERE user_id=? AND company_id=?', (user['id'], company_id))]
            if not assigned:
                raise HTTPException(403, 'No properties have been assigned to your inspection account.')
            marks = ','.join('?' for _ in assigned)
            result['properties'] = [dict(r) for r in conn.execute(f'SELECT * FROM properties WHERE company_id=? AND id IN ({marks}) ORDER BY id DESC', (company_id, *assigned))]
            result['contracts'] = [dict(r) for r in conn.execute(f'SELECT * FROM contracts WHERE company_id=? AND property_id IN ({marks}) ORDER BY id DESC', (company_id, *assigned))]
            contract_ids = [r['id'] for r in result['contracts']]
            contract_marks = ','.join('?' for _ in contract_ids)
            for table in ('tenants','repairs','inspections','tasks'):
                if contract_ids:
                    result[table] = [dict(r) for r in conn.execute(f'SELECT * FROM {table} WHERE company_id=? AND contract_id IN ({contract_marks}) ORDER BY id DESC', (company_id, *contract_ids))]
                else:
                    result[table] = []
            result['communications'] = []
            result['documents'] = [dict(r) for r in conn.execute(f'SELECT d.id,d.company_id,d.contract_id,d.name,d.kind,d.mime,d.size,d.sha256,d.created_at,(SELECT COUNT(*) FROM shared_documents s WHERE s.document_id=d.id AND s.revoked=0) shared_count,(SELECT group_concat(s.contract_id) FROM shared_documents s WHERE s.document_id=d.id AND s.revoked=0) shared_contracts FROM documents d WHERE d.company_id=? AND d.contract_id IN ({contract_marks}) AND d.kind NOT IN ("payment proof","supplier invoice","deposit evidence") ORDER BY d.id DESC', (company_id, *contract_ids))] if contract_ids else []
        for table in (operational if role != 'inspector' else []) + (financial if role in ('admin', 'manager', 'finance') else ['rent_versions', 'valuations'] if role == 'property' else []):
            result[table] = [dict(r) for r in conn.execute(f'SELECT * FROM {table} WHERE company_id=? ORDER BY id DESC', (company_id,))]
        if role in ('admin', 'manager', 'finance'):
            result['service_charge_groups'] = [dict(r) for r in conn.execute('''SELECT g.*, GROUP_CONCAT(p.id || ':' || p.name || ':' || printf('%.4f',u.share_percent), '|') AS units
                FROM service_charge_groups g JOIN service_charge_group_units u ON u.group_id=g.id JOIN properties p ON p.id=u.property_id
                WHERE g.company_id=? GROUP BY g.id ORDER BY g.id DESC''', (company_id,))]
        if role != 'inspector':
            result['documents'] = [dict(r) for r in conn.execute('SELECT d.id,d.company_id,d.contract_id,d.name,d.kind,d.mime,d.size,d.sha256,d.created_at,(SELECT COUNT(*) FROM shared_documents s WHERE s.document_id=d.id AND s.revoked=0) shared_count,(SELECT group_concat(s.contract_id) FROM shared_documents s WHERE s.document_id=d.id AND s.revoked=0) shared_contracts FROM documents d WHERE d.company_id=? AND (? IN ("admin","manager","finance") OR d.kind NOT IN ("payment proof","supplier invoice","deposit evidence")) ORDER BY d.id DESC', (company_id, role))]
        if role in ('admin','manager','finance'):
            result['arrears_cases']=[{**dict(r),'balance_cents':invoice_balance(conn,r['invoice_id'])} for r in conn.execute('SELECT a.*,i.number invoice_number,i.due_date,t.name tenant_name,p.name property_name FROM arrears_cases a JOIN invoices i ON i.id=a.invoice_id JOIN contracts c ON c.id=a.contract_id JOIN tenants t ON t.id=c.tenant_id JOIN properties p ON p.id=c.property_id WHERE a.company_id=? ORDER BY a.updated_at DESC',(company_id,))]
            result['arrears_events']=[dict(r) for r in conn.execute('SELECT e.*,u.name actor_name FROM arrears_events e LEFT JOIN users u ON u.id=e.created_by WHERE e.company_id=? ORDER BY e.id DESC',(company_id,))]
            result['outbound_emails']=[dict(r) for r in conn.execute('SELECT id,invoice_id,kind,recipient,status,attempts,next_attempt_at,last_error,created_at,sent_at FROM outbound_email_jobs WHERE company_id=? ORDER BY id DESC LIMIT 200',(company_id,))]
        if role in ('admin', 'manager'):
            result['audit'] = [dict(r) for r in conn.execute('SELECT a.id,a.created_at,a.action,a.entity,a.entity_id,a.detail,a.hash,u.name user_name FROM audit a LEFT JOIN users u ON u.id=a.user_id WHERE a.company_id=? ORDER BY a.id DESC LIMIT 250', (company_id,))]
            result['staff'] = [dict(r) for r in conn.execute('SELECT u.id,u.name,u.email,m.role FROM users u JOIN memberships m ON m.user_id=u.id WHERE m.company_id=?', (company_id,))]
        from .operations import staff_extras
        result.update(staff_extras(conn, user, company_id, role))
        if role != 'inspector':
            from .schedule import payment_schedule
            result['payment_schedules'] = [payment_schedule(conn, c) for c in result.get('contracts', []) if c['status'] in ('active', 'notice given')]
        if role in ('admin', 'manager', 'finance'):
            from .paging import apply_window
            apply_window(conn, company_id, result)
    db.audit_view(user['id'], company_id, 'workspace', company_id, {'role': role})
    return result


@app.post('/api/tenants/{tenant_id}/archive')
def archive_tenant(tenant_id: int, data: dict, user=Depends(current_user)):
    company_id = integer(data, 'company_id')
    with transaction() as conn:
        authorize(conn, user, company_id, OPERATIONS)
        tenant = linked(conn, 'tenants', tenant_id, company_id)
        active = conn.execute("SELECT 1 FROM contracts WHERE company_id=? AND tenant_id=? AND status IN ('active','notice given') AND start_date<=? AND (end_date IS NULL OR end_date>=?)", (company_id, tenant_id, local_today().isoformat(), local_today().isoformat())).fetchone()
        if active:
            raise HTTPException(409, 'This tenant has an active tenancy. Record the tenancy end date before removing the contact.')
        if tenant['archived_at']:
            return {'archived': True}
        conn.execute('UPDATE tenants SET archived_at=? WHERE id=? AND company_id=?', (now(), tenant_id, company_id))
        conn.execute('UPDATE portal_accounts SET enabled=0 WHERE tenant_id=? AND company_id=?', (tenant_id, company_id))
        # free the email: a login that belonged only to this tenant is closed and anonymised, so the same person can be invited again later
        for row in conn.execute('SELECT user_id FROM portal_accounts WHERE tenant_id=? AND company_id=?', (tenant_id, company_id)).fetchall():
            uid = row['user_id']
            other = (conn.execute('SELECT 1 FROM memberships WHERE user_id=?', (uid,)).fetchone() or conn.execute('SELECT 1 FROM owner_accounts WHERE user_id=?', (uid,)).fetchone()
                     or conn.execute('SELECT 1 FROM contractor_accounts WHERE user_id=?', (uid,)).fetchone()
                     or conn.execute('SELECT 1 FROM portal_accounts WHERE user_id=? AND enabled=1', (uid,)).fetchone())
            if not other:
                conn.execute("UPDATE users SET active=0,name='Removed tenant',email='removed-'||id||'@removed.invalid',password_hash='!' WHERE id=?", (uid,))
                conn.execute('DELETE FROM sessions WHERE user_id=?', (uid,))
        conn.execute("UPDATE account_invitations SET revoked=1 WHERE kind='tenant' AND target_id=? AND company_id=? AND used_at IS NULL", (tenant_id, company_id))
        audit(conn, user['id'], company_id, 'archived', 'tenant', tenant_id, {'name': tenant['name']})
    return {'archived': True}


@app.post('/api/tenants/{tenant_id}/restore')
def restore_tenant(tenant_id: int, data: dict, user=Depends(current_user)):
    company_id = integer(data, 'company_id')
    with transaction() as conn:
        authorize(conn, user, company_id, OPERATIONS)
        tenant = linked(conn, 'tenants', tenant_id, company_id)
        if not tenant['archived_at']:
            return {'archived': False}
        conn.execute('UPDATE tenants SET archived_at=NULL WHERE id=? AND company_id=?', (tenant_id, company_id))
        conn.execute('UPDATE portal_accounts SET enabled=1 WHERE tenant_id=? AND company_id=?', (tenant_id, company_id))
        audit(conn, user['id'], company_id, 'restored', 'tenant', tenant_id, {'name': tenant['name']})
    return {'archived': False}


@app.post('/api/companies')
def create_company(data: dict, user=Depends(current_user)):
    if not user['global_admin']:
        raise HTTPException(403, 'Only the system administrator can add companies.')
    with transaction() as conn:
        company_id = insert(conn, 'companies', {'name': text_field(data, 'name', maximum=120), 'kvk': str(data.get('kvk', ''))[:50], 'address': str(data.get('address', ''))[:500], 'payment_instructions': str(data.get('payment_instructions', ''))[:2000], 'created_at': now()})
        conn.execute('INSERT INTO memberships VALUES(?,?,?)', (user['id'], company_id, 'admin'))
        audit(conn, user['id'], company_id, 'created', 'company', company_id, {'name': data['name']})
    return {'id': company_id}


@app.post('/api/staff')
def create_staff(data: dict, user=Depends(current_user)):
    company_id = integer(data, 'company_id')
    with transaction() as conn:
        authorize(conn, user, company_id, ('admin',))
        email = text_field(data, 'email', maximum=254).lower()
        if '@' not in email:
            raise HTTPException(422, 'Enter a valid email address.')
        role = choice(data, 'role', ('admin', 'manager', 'finance', 'property', 'inspector'))
        if conn.execute('SELECT id FROM users WHERE email=?', (email,)).fetchone():
            raise HTTPException(409, 'That account already exists. Company access must be assigned by a system administrator through the CLI.')
        password = text_field(data, 'password', maximum=128)
        if len(password) < 12:
            raise HTTPException(422, 'Use a password of at least 12 characters.')
        uid = insert(conn, 'users', {'name': text_field(data, 'name', maximum=120), 'email': email, 'password_hash': password_hash(password)})
        conn.execute('INSERT INTO memberships VALUES(?,?,?)', (uid, company_id, role))
        audit(conn, user['id'], company_id, 'created', 'staff', uid, {'email': email, 'role': role})
    return {'id': uid}


@app.post('/api/records/{kind}')
def create_record(kind: str, data: dict, user=Depends(current_user)):
    company_id = integer(data, 'company_id')
    with transaction() as conn:
        allowed_roles = ('inspector','property','finance','manager') if kind == 'inspections' else FINANCE if kind in ('cost_lines', 'rent_proposals', 'rules', 'valuations') else ('property', 'finance', 'manager') if kind in ('tasks', 'communications') else OPERATIONS
        role = authorize(conn, user, company_id, allowed_roles)
        values = {'company_id': company_id, 'created_at': now()}
        if kind == 'properties':
            values.update({k: text_field(data, k, maximum=200) for k in ('name', 'address', 'city')})
            values.update({'postcode': str(data.get('postcode', ''))[:20], 'kind': choice(data, 'kind', ('Apartment', 'House', 'Room', 'Parking', 'Storage'), 'Apartment'), 'energy_label': choice(data, 'energy_label', ('A++++','A+++','A++','A+','A','B','C','D','E','F','G','Unknown'), 'Unknown'), 'rooms': integer(data, 'rooms', 1), 'bag_id': str(data.get('bag_id', ''))[:40]})
            try:
                values['area'] = float(data.get('area', 0))
                if not 0 <= values['area'] <= 100000 or not 0 <= values['rooms'] <= 1000:
                    raise ValueError()
            except (ValueError, TypeError):
                raise HTTPException(422, 'Enter a valid area and room count.')
        elif kind == 'tenants':
            values.update({'name': text_field(data, 'name', maximum=120), 'email': text_field(data, 'email', maximum=254), 'phone': str(data.get('phone', ''))[:50], 'language': choice(data, 'language', ('nl','en'), 'nl')})
            if '@' not in values['email']:
                raise HTTPException(422, 'Enter a valid tenant email.')
        elif kind == 'contracts':
            prop = linked(conn, 'properties', integer(data, 'property_id'), company_id)
            tenant = linked(conn, 'tenants', integer(data, 'tenant_id'), company_id)
            start, signed = date_field(data, 'start_date'), date_field(data, 'signed_on')
            end = date_field(data, 'end_date') if data.get('end_date') else None
            if signed > start:
                raise HTTPException(422, 'The signing date must be on or before the tenancy start date.')
            if end and end < start:
                raise HTTPException(422, 'End date must follow start date.')
            overlap = conn.execute('SELECT id FROM contracts WHERE property_id=? AND start_date<=? AND (end_date IS NULL OR end_date>=?)', (prop['id'], end or '9999-12-31', start)).fetchone()
            if overlap:
                raise HTTPException(409, 'This unit already has a contract during that period.')
            b, s = money(data, 'basic_rent'), money(data, 'service_charge', True)
            values.update({'property_id': prop['id'], 'tenant_id': tenant['id'], 'signed_on': signed, 'start_date': start, 'end_date': end, 'deposit_cents': money(data, 'deposit', True)})
            record_id = insert(conn, kind, values)
            insert(conn, 'rent_versions', {'company_id': company_id, 'contract_id': record_id, 'effective_from': start, 'basic_cents': b, 'service_cents': s, 'created_at': now(), 'created_by': user['id']})
            task(conn, company_id, record_id, 'Verify signed contract, WWS report and letting documents', start, 'Contract')
            task(conn, company_id, record_id, 'Complete move-in inspection', start, 'Inspection')
            audit(conn, user['id'], company_id, 'created', 'contracts', record_id, values)
            return {'id': record_id}
        elif kind == 'repairs':
            linked(conn, 'contracts', integer(data, 'contract_id'), company_id)
            reported = date_field(data, 'reported_on', local_today().isoformat())
            if reported > local_today().isoformat():
                raise HTTPException(422, 'A report cannot be dated in the future.')
            urgency_value = choice(data, 'urgency', ('Routine','Urgent','Emergency'))
            stamped = sla.stamp(conn, company_id, urgency_value, reported)
            due = stamped['due_date']
            values.update({'contract_id': integer(data, 'contract_id'), 'title': text_field(data, 'title', maximum=180), 'category': choice(data, 'category', ('Plumbing','Heating','Electrical','Damp / mould','Appliance','General')), 'urgency': choice(data, 'urgency', ('Routine','Urgent','Emergency')), 'description': text_field(data, 'description'), 'reported_on': reported, 'written': 1 if data.get('written', True) else 0, 'due_date': due, 'response_due_at': stamped['response_due_at'], 'assigned_to': str(data.get('assigned_to',''))[:120]})
        elif kind == 'tasks':
            contract_id = integer(data, 'contract_id') if data.get('contract_id') else None
            if contract_id:
                linked(conn, 'contracts', contract_id, company_id)
            values.update({'contract_id': contract_id, 'title': text_field(data, 'title', maximum=200), 'due_date': date_field(data, 'due_date'), 'owner': text_field(data, 'owner', user['name'], 120), 'category': text_field(data, 'category', 'General', 80)})
        elif kind == 'communications':
            linked(conn, 'contracts', integer(data, 'contract_id'), company_id)
            values.update({'contract_id': integer(data, 'contract_id'), 'channel': choice(data, 'channel', ('Email','Phone','WhatsApp','Letter','Visit')), 'direction': choice(data, 'direction', ('Inbound','Outbound')), 'subject': text_field(data, 'subject', maximum=200), 'body': text_field(data, 'body'), 'status': choice(data, 'status', ('Recorded','Draft','Manually sent')), 'created_by': user['id']})
        elif kind == 'inspections':
            contract = linked(conn, 'contracts', integer(data, 'contract_id'), company_id)
            if role == 'inspector':
                from .security import authorize_property
                authorize_property(conn, user, company_id, contract['property_id'])
            doc_id = integer(data, 'document_id') if data.get('document_id') else None
            if doc_id:
                linked(conn, 'documents', doc_id, company_id)
            values.update({'contract_id': integer(data, 'contract_id'), 'kind': choice(data, 'kind', ('Move-in','Move-out','Periodic','Repair')), 'inspected_on': date_field(data, 'inspected_on'), 'findings': text_field(data, 'findings'), 'document_id': doc_id, 'created_by': user['id']})
        elif kind == 'valuations':
            linked(conn, 'properties', integer(data, 'property_id'), company_id)
            linked(conn, 'documents', integer(data, 'document_id'), company_id)
            try:
                points = float(data.get('points', 0))
                if not 0 < points <= 2000:
                    raise ValueError()
            except (TypeError, ValueError):
                raise HTTPException(422, 'Enter valid WWS points.')
            values.update({'property_id': integer(data, 'property_id'), 'points': points, 'max_rent_cents': money(data, 'max_rent'), 'reference_date': date_field(data, 'reference_date'), 'source': text_field(data, 'source'), 'document_id': integer(data, 'document_id'), 'created_by': user['id']})
        elif kind == 'cost_lines':
            raw_document_id = data.get('document_id')
            document_id = integer(data, 'document_id') if raw_document_id is not None and str(raw_document_id).strip() else None
            if document_id:
                linked(conn, 'documents', document_id, company_id)
            entry_method = choice(data, 'entry_method', ('invoice','manual'), 'manual')
            if entry_method == 'invoice' and not document_id:
                raise HTTPException(422, 'Attach the supplier invoice, or choose manual entry.')
            year = integer(data, 'year')
            if not 2000 <= year <= 2100:
                raise HTTPException(422, 'Year must be between 2000 and 2100.')
            group_id = integer(data, 'service_group_id') if data.get('service_group_id') else None
            if group_id:
                group = conn.execute('SELECT * FROM service_charge_groups WHERE id=? AND company_id=?', (group_id, company_id)).fetchone()
                if not group:
                    raise HTTPException(404, 'Building group not found.')
                members = conn.execute('SELECT property_id FROM service_charge_group_units WHERE group_id=? ORDER BY property_id', (group_id,)).fetchall()
                if not members:
                    raise HTTPException(422, 'Add units to this building group first.')
                property_id = members[0]['property_id']
                method = choice(data, 'allocation_method', ('equal','floor_area','fixed_percentage'))
                if conn.execute('SELECT id FROM settlements WHERE service_group_id=? AND year=?', (group_id, year)).fetchone():
                    raise HTTPException(409, 'A settlement already freezes this building group and year.')
            else:
                property_id = integer(data, 'property_id')
                linked(conn, 'properties', property_id, company_id)
                method = 'tenancy_days'
                if conn.execute('SELECT id FROM settlements WHERE property_id=? AND year=? AND service_group_id IS NULL', (property_id, year)).fetchone():
                    raise HTTPException(409, 'A settlement already freezes this property and year.')
            values.update({'property_id': property_id, 'service_group_id': group_id, 'allocation_method': method, 'year': year, 'category': text_field(data, 'category', maximum=120), 'supplier': text_field(data, 'supplier', maximum=120), 'amount_cents': money(data, 'amount'), 'document_id': document_id, 'entry_method':entry_method, 'created_by': user['id']})
        elif kind == 'rent_proposals':
            contract = linked(conn, 'contracts', integer(data, 'contract_id'), company_id)
            effective = date_field(data, 'effective_from')
            if effective <= local_today().isoformat() or (contract['end_date'] and effective > contract['end_date']):
                raise HTTPException(422, 'Choose a future date within the tenancy.')
            if conn.execute('SELECT id FROM invoices WHERE contract_id=? AND period>=?', (contract['id'], effective[:7])).fetchone():
                raise HTTPException(409, 'Invoices already exist for the proposed effective period.')
            values.update({'contract_id': contract['id'], 'effective_from': effective, 'basic_cents': money(data, 'basic_rent'), 'service_cents': money(data, 'service_charge', True), 'reason': text_field(data, 'reason'), 'source': text_field(data, 'source'), 'created_by': user['id']})
        elif kind == 'rules':
            values.update({'name': text_field(data, 'name', maximum=120), 'value': text_field(data, 'value', maximum=500), 'effective_from': date_field(data, 'effective_from'), 'source': text_field(data, 'source'), 'created_by': user['id']})
        else:
            raise HTTPException(404, 'Unknown record type.')
        record_id = insert(conn, kind, values)
        if kind == 'repairs':
            task(conn, company_id, values['contract_id'], f'Review repair #{record_id}: {values["title"]}', (date.fromisoformat(reported)+timedelta(days=35)).isoformat() if values['written'] else reported, 'Repair', values['assigned_to'] or 'Property team')
            if values['urgency'] == 'Emergency':
                task(conn, company_id, values['contract_id'], f'Immediate response: repair #{record_id}', reported, 'Emergency', 'Property team')
        audit(conn, user['id'], company_id, 'created', kind, record_id, values)
        return {'id': record_id}


PORTFOLIO_IMPORT_COLUMNS = ('property_name','address','city','postcode','kind','area','rooms','energy_label','bag_id','tenant_name','tenant_email','tenant_phone','tenant_language','signed_on','start_date','end_date','basic_rent','service_charge','deposit')


async def _read_portfolio_import(file: UploadFile):
    raw = await file.read(5_000_001)
    if len(raw) > 5_000_000:
        raise HTTPException(413, 'Portfolio CSV files are limited to 5 MB.')
    try:
        reader = csv.DictReader(io.StringIO(raw.decode('utf-8-sig')))
        headers = {str(h or '').strip().lower() for h in (reader.fieldnames or [])}
        if not {'property_name','address','city'}.issubset(headers):
            raise ValueError('CSV must include property_name, address, and city columns. Download the template for all supported columns.')
        rows=[]
        for raw_row in reader:
            if raw_row.get(None):
                raise ValueError(f'CSV row {reader.line_num}: more values than the header. Check the commas and try again.')
            row={str(k).strip().lower():str(v or '').strip() for k,v in raw_row.items() if k is not None}
            if any(row.values()):
                rows.append(row)
        if not rows or len(rows) > 1000:
            raise ValueError('Include between 1 and 1,000 rows.')
        return rows
    except (UnicodeDecodeError, csv.Error, ValueError) as exc:
        raise HTTPException(422, str(exc))


def _apply_portfolio_import(conn, company_id, rows, user):
    authorize(conn, user, company_id, ('property','manager'))
    properties = {}
    tenants = {}
    counts = {'properties_created':0,'tenants_created':0,'contracts_created':0,'rows':len(rows)}
    for index, row in enumerate(rows, start=2):
        try:
            email=str(row.get('tenant_email','')).strip().lower()
            tenant_name=str(row.get('tenant_name','')).strip()
            name=str(row.get('property_name','')).strip()
            address=str(row.get('address','')).strip()
            city=str(row.get('city','')).strip()
            start_raw=str(row.get('start_date','')).strip()
            signed_raw=str(row.get('signed_on','')).strip()
            tenancy_values=any(str(row.get(k,'')).strip() for k in ('signed_on','start_date','end_date','basic_rent','service_charge','deposit'))
            if not name and not email:
                raise HTTPException(422,'Provide a property_name or tenant_email for each row.')
            prop=None
            if name:
                if not address or not city:
                    raise HTTPException(422,'address and city are required when property_name is provided.')
                pkey = name.casefold()
                prop = properties.get(pkey)
                if prop is None:
                    prop = conn.execute('SELECT * FROM properties WHERE company_id=? AND name=? COLLATE NOCASE',(company_id,name)).fetchone()
                    if prop:
                        if prop['address'].casefold()!=address.casefold() or prop['city'].casefold()!=city.casefold():
                            raise HTTPException(422, f'Property "{name}" already exists with a different address or city.')
                        prop = dict(prop)
                    else:
                        kind = choice({'kind':row.get('kind') or 'Apartment'},'kind',('Apartment','House','Room','Parking','Storage'))
                        try:
                            area=float(row.get('area') or 0); rooms=int(row.get('rooms') or 1)
                            if not 0<=area<=100000 or not 0<=rooms<=1000: raise ValueError()
                        except (ValueError,TypeError):
                            raise HTTPException(422,'area and rooms must be valid non-negative numbers.')
                        energy=choice({'energy_label':row.get('energy_label') or 'Unknown'},'energy_label',('Unknown','A++++','A+++','A++','A+','A','B','C','D','E','F','G'))
                        pid=insert(conn,'properties',{'company_id':company_id,'name':name,'address':address,'city':city,'postcode':str(row.get('postcode',''))[:20],'kind':kind,'rooms':rooms,'area':area,'energy_label':energy,'bag_id':str(row.get('bag_id',''))[:40],'created_at':now()})
                        prop=dict(conn.execute('SELECT * FROM properties WHERE id=?',(pid,)).fetchone())
                        counts['properties_created']+=1
                    properties[pkey]=prop
            if not email:
                if tenancy_values:
                    raise HTTPException(422,'tenant_email and tenant details are required for a tenancy row.')
                continue
            if '@' not in email or not tenant_name:
                raise HTTPException(422,'Provide a valid tenant_email and tenant_name.')
            tenant=tenants.get(email)
            if tenant is None:
                existing=conn.execute('SELECT * FROM tenants WHERE company_id=? AND email=? COLLATE NOCASE',(company_id,email)).fetchone()
                if existing:
                    if existing['name'].strip().casefold()!=tenant_name.casefold():
                        raise HTTPException(422,f'Tenant email {email} already exists with a different name.')
                    tenant=dict(existing)
                else:
                    language=choice({'tenant_language':row.get('tenant_language') or 'nl'},'tenant_language',('nl','en'))
                    tid=insert(conn,'tenants',{'company_id':company_id,'name':tenant_name,'email':email,'phone':str(row.get('tenant_phone',''))[:50],'language':language,'created_at':now()})
                    tenant=dict(conn.execute('SELECT * FROM tenants WHERE id=?',(tid,)).fetchone())
                    counts['tenants_created']+=1
                tenants[email]=tenant
            elif tenant['name'].strip().casefold()!=tenant_name.casefold():
                raise HTTPException(422,f'Tenant email {email} is used for two different names in this file.')

            if not start_raw:
                if tenancy_values:
                    raise HTTPException(422,'start_date is required when tenancy dates or rent are supplied.')
                continue
            if not prop:
                raise HTTPException(422,'property_name, address, and city are required for a contract row.')
            signed=date_field({'date':signed_raw},'date')
            start=date_field({'date':start_raw},'date')
            end=date_field({'date':row.get('end_date')},'date') if row.get('end_date') else None
            if signed>start: raise HTTPException(422,'signed_on must be on or before start_date.')
            if end and end<start: raise HTTPException(422,'end_date must be on or after start_date.')
            if conn.execute('SELECT id FROM contracts WHERE property_id=? AND start_date<=? AND (end_date IS NULL OR end_date>=?)',(prop['id'],end or '9999-12-31',start)).fetchone():
                raise HTTPException(409,'This property already has a contract during that period.')
            basic=money(row,'basic_rent')
            service=money({'service_charge':row.get('service_charge') or '0'},'service_charge',True)
            deposit=money({'deposit':row.get('deposit') or '0'},'deposit',True)
            cid=insert(conn,'contracts',{'company_id':company_id,'property_id':prop['id'],'tenant_id':tenant['id'],'signed_on':signed,'start_date':start,'end_date':end,'status':'active','deposit_cents':deposit,'created_at':now()})
            insert(conn,'rent_versions',{'company_id':company_id,'contract_id':cid,'effective_from':start,'basic_cents':basic,'service_cents':service,'created_at':now(),'created_by':user['id']})
            task(conn,company_id,cid,'Verify signed contract, WWS report and letting documents',start,'Contract')
            task(conn,company_id,cid,'Complete move-in inspection',start,'Inspection')
            counts['contracts_created']+=1
        except HTTPException as exc:
            raise HTTPException(exc.status_code,f'CSV row {index}: {exc.detail}')
    return counts


async def _portfolio_import(file, company_id, user, commit):
    rows=await _read_portfolio_import(file)
    conn=db.connect()
    try:
        conn.execute('BEGIN IMMEDIATE')
        counts=_apply_portfolio_import(conn,company_id,rows,user)
        if commit:
            audit(conn,user['id'],company_id,'portfolio_imported','portfolio',None,{k:v for k,v in counts.items() if k!='rows'})
            conn.commit()
        else:
            conn.rollback()
        return counts
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@app.post('/api/portfolio/import/preview')
async def portfolio_import_preview(company_id:int=Form(...),file:UploadFile=File(...),user=Depends(current_user)):
    return await _portfolio_import(file,company_id,user,False)


@app.post('/api/portfolio/import')
async def portfolio_import(company_id:int=Form(...),file:UploadFile=File(...),user=Depends(current_user)):
    return await _portfolio_import(file,company_id,user,True)


@app.post('/api/service-charge-costs/import')
async def import_service_charge_costs(company_id: int = Form(...), file: UploadFile = File(...), user=Depends(current_user)):
    raw = await file.read(1_000_001)
    if len(raw) > 1_000_000:
        raise HTTPException(413, 'CSV imports are limited to 1 MB.')
    try:
        content = raw.decode('utf-8-sig')
        reader = csv.DictReader(io.StringIO(content))
        required = {'scope_type','scope_name','year','category','supplier','amount','allocation_method'}
        if not reader.fieldnames or not required.issubset({x.strip() for x in reader.fieldnames}):
            raise ValueError('CSV needs these columns: scope_type, scope_name, year, category, supplier, amount, allocation_method. document_name is optional.')
        rows = list(reader)
        if not rows or len(rows) > 500:
            raise ValueError('Include between 1 and 500 cost rows.')
    except (UnicodeDecodeError, csv.Error, ValueError) as exc:
        raise HTTPException(422, str(exc))
    created=[]
    with transaction() as conn:
        authorize(conn, user, company_id, FINANCE)
        for index, raw_row in enumerate(rows, start=2):
            row={str(k).strip():str(v or '').strip() for k,v in raw_row.items() if k is not None}
            kind=row.get('scope_type','').lower()
            name=row.get('scope_name','')
            if kind=='building':
                scope=conn.execute('SELECT * FROM service_charge_groups WHERE company_id=? AND name=? COLLATE NOCASE',(company_id,name)).fetchone()
                if not scope:
                    raise HTTPException(422,f'CSV row {index}: building group "{name}" was not found.')
                members=conn.execute('SELECT property_id FROM service_charge_group_units WHERE group_id=? ORDER BY property_id',(scope['id'],)).fetchall()
                if not members:
                    raise HTTPException(422,f'CSV row {index}: the building group has no units.')
                property_id=members[0]['property_id'];group_id=scope['id']
                try: method=choice(row,'allocation_method',('equal','floor_area','fixed_percentage'))
                except HTTPException: raise HTTPException(422,f'CSV row {index}: allocation_method must be equal, floor_area, or fixed_percentage for building costs.')
                frozen=conn.execute('SELECT 1 FROM settlements WHERE service_group_id=? AND year=?',(group_id,integer(row,'year'))).fetchone()
            elif kind=='unit':
                scope=conn.execute('SELECT * FROM properties WHERE company_id=? AND name=? COLLATE NOCASE',(company_id,name)).fetchone()
                if not scope:
                    raise HTTPException(422,f'CSV row {index}: unit "{name}" was not found.')
                property_id=scope['id'];group_id=None;method='tenancy_days'
                frozen=conn.execute('SELECT 1 FROM settlements WHERE property_id=? AND year=? AND service_group_id IS NULL',(property_id,integer(row,'year'))).fetchone()
            else:
                raise HTTPException(422,f'CSV row {index}: scope_type must be unit or building.')
            year=integer(row,'year')
            if not 2000<=year<=2100:
                raise HTTPException(422,f'CSV row {index}: year must be between 2000 and 2100.')
            if frozen:
                raise HTTPException(409,f'CSV row {index}: a settlement already freezes this scope and year.')
            doc_name=row.get('document_name','')
            doc=conn.execute('SELECT id FROM documents WHERE company_id=? AND name=? COLLATE NOCASE',(company_id,doc_name)).fetchone() if doc_name else None
            if doc_name and not doc:
                raise HTTPException(422,f'CSV row {index}: document "{doc_name}" was not found in Documents.')
            values={'company_id':company_id,'property_id':property_id,'service_group_id':group_id,'allocation_method':method,'entry_method':'import','year':year,'category':text_field(row,'category',maximum=120),'supplier':text_field(row,'supplier',maximum=120),'amount_cents':money(row,'amount'),'document_id':doc['id'] if doc else None,'created_by':user['id'],'created_at':now()}
            cost_id=insert(conn,'cost_lines',values);created.append(cost_id)
        audit(conn,user['id'],company_id,'imported','service_charge_costs',None,{'count':len(created),'file_name':(file.filename or '')[:160]})
    return {'imported':len(created),'ids':created}


@app.post('/api/invoices/run')
def invoice_run(data: dict, user=Depends(current_user)):
    company = integer(data, 'company_id')
    with transaction() as conn:
        authorize(conn, user, company, FINANCE)
        period = text_field(data, 'period', maximum=7)
        count = issue_month(conn, company, period, user['id'])
        why = None
        if not count:
            # say why nothing was created, so an empty run is not a mystery
            first = date.fromisoformat(period + '-01')
            last = date(first.year, first.month, calendar.monthrange(first.year, first.month)[1])
            rows_ = conn.execute('SELECT id,start_date,end_date,status FROM contracts WHERE company_id=?', (company,)).fetchall()
            live = [r for r in rows_ if r['status'] in ('active', 'notice given')]
            covering = [r for r in live if r['start_date'] <= last.isoformat() and (not r['end_date'] or r['end_date'] >= first.isoformat())]
            done = [r for r in covering if conn.execute('SELECT 1 FROM invoices WHERE contract_id=? AND period=?', (r['id'], period)).fetchone()]
            later = [r for r in live if r['start_date'] > last.isoformat()]
            if not rows_:
                why = 'There are no tenancies yet.'
            elif done and len(done) == len(covering):
                why = 'Every tenancy active in this month already has its invoice.'
            elif later and not covering:
                why = f'No tenancy is active in {period}. The earliest one starts on {min(r["start_date"] for r in later)}. Pick a month on or after that date.'
            elif not covering:
                why = f'No tenancy is active in {period} (they have ended or have not been approved yet).'
    from .email_delivery import deliver_pending_emails
    delivery = deliver_pending_emails(company_id=company)
    return {'created': count, 'email_delivery': delivery, 'why': why}


@app.post('/api/invoices/{invoice_id}/link')
def create_upload_link(invoice_id: int, user=Depends(current_user)):
    with transaction() as conn:
        inv = record(conn, 'invoices', invoice_id, user, FINANCE)
        conn.execute('UPDATE upload_links SET revoked=1 WHERE invoice_id=?', (invoice_id,))
        token = secrets.token_urlsafe(32)
        conn.execute('INSERT INTO upload_links(invoice_id,token_hash,expires_at,created_by) VALUES(?,?,?,?)', (invoice_id, token_hash(token), expiry(30), user['id']))
        audit(conn, user['id'], inv['company_id'], 'link_created', 'invoice', invoice_id, {'expires_in_days': 30})
    return {'path': '/pay/' + token, 'expires_in_days': 30}


@app.post('/api/invoices/{invoice_id}/revoke-link')
def revoke_link(invoice_id: int, user=Depends(current_user)):
    with transaction() as conn:
        inv = record(conn, 'invoices', invoice_id, user, FINANCE)
        conn.execute('UPDATE upload_links SET revoked=1 WHERE invoice_id=?', (invoice_id,))
        audit(conn, user['id'], inv['company_id'], 'link_revoked', 'invoice', invoice_id, {})
    return {'ok': True}


def public_invoice(conn, token):
    inv = conn.execute('SELECT i.* FROM upload_links l JOIN invoices i ON i.id=l.invoice_id WHERE l.token_hash=? AND l.revoked=0 AND l.expires_at>?', (token_hash(token), now())).fetchone()
    if not inv:
        raise HTTPException(404, 'This link has expired or was revoked. Contact your property manager for a new link.')
    return dict(inv)


@app.get('/api/public/{token}')
def public_details(token: str):
    with connect() as conn:
        inv = public_invoice(conn, token)
        snap = json.loads(inv['snapshot'])
        proofs = [dict(r) for r in conn.execute('SELECT amount_cents,paid_on,status,review_note,created_at FROM payment_proofs WHERE invoice_id=? ORDER BY id DESC', (inv['id'],))]
        return {'number': inv['number'], 'period': inv['period'], 'due_date': inv['due_date'], 'total_cents': inv['total_cents'], 'balance_cents': invoice_balance(conn, inv['id']), 'tenant_name': snap['tenant_name'], 'property': snap['address'], 'company': snap['company_name'], 'payment_instructions': snap['payment_instructions'], 'submissions': proofs}


async def read_upload(file):
    content = await file.read(10 * 1024 * 1024 + 1)
    await file.close()
    if not content or len(content) > 10 * 1024 * 1024:
        raise HTTPException(422, 'Upload a file between 1 byte and 10 MB.')
    if content.startswith(b'%PDF-'):
        mime, ext = 'application/pdf', '.pdf'
    elif content.startswith(b'\x89PNG\r\n\x1a\n'):
        mime, ext = 'image/png', '.png'
    elif content.startswith(b'\xff\xd8\xff'):
        mime, ext = 'image/jpeg', '.jpg'
    else:
        raise HTTPException(422, 'Only PDF, PNG, and JPEG files are accepted.')
    filename = Path((file.filename or 'document').replace('\\', '/')).name[:180]
    return content, mime, ext, filename


def save_document(conn, company, contract_id, user_id, kind, upload):
    content, mime, ext, filename = upload
    key = secrets.token_hex(24) + ext
    (db.DATA_DIR / 'uploads' / key).write_bytes(content)
    if hasattr(conn, 'pending_files'):
        conn.pending_files.append(db.DATA_DIR / 'uploads' / key)
    return insert(conn, 'documents', {'company_id': company, 'contract_id': contract_id, 'name': filename, 'kind': kind, 'mime': mime, 'size': len(content), 'sha256': hashlib.sha256(content).hexdigest(), 'file_key': key, 'created_by': user_id, 'created_at': now()})


@app.post('/api/documents')
async def upload_document(company_id: int = Form(...), kind: str = Form('General'), contract_id: str = Form(''), file: UploadFile = File(...), user=Depends(current_user)):
    # Authorize before reading the attachment body into memory.
    with connect() as conn:
        authorize(conn, user, company_id, ('property', 'finance', 'manager'))
        cid = integer({'contract_id': contract_id}, 'contract_id') if contract_id else None
        if cid:
            linked(conn, 'contracts', cid, company_id)
    upload = await read_upload(file)
    with transaction() as conn:
        doc_id = save_document(conn, company_id, cid, user['id'], kind[:80], upload)
        audit(conn, user['id'], company_id, 'uploaded', 'document', doc_id, {'name': upload[3]})
    return {'id': doc_id}


@app.get('/api/documents/{document_id}/file')
def get_document(document_id: int, download: int = 0, user=Depends(current_user)):
    with transaction() as conn:
        doc = record(conn, 'documents', document_id, user, ('inspector','property','finance','manager'))
        role = authorize(conn, user, doc['company_id'])
        if role == 'property' and doc['kind'] in ('payment proof','supplier invoice','deposit evidence'):
            raise HTTPException(403, 'Financial document access is restricted.')
        if role == 'inspector' and doc['kind'] in ('payment proof','supplier invoice','deposit evidence','Contractor agreement'):
            raise HTTPException(403, 'This document is outside your inspection access.')
        audit(conn, user['id'], doc['company_id'], 'downloaded' if download else 'viewed', 'document', document_id, {})
    return FileResponse(db.DATA_DIR / 'uploads' / doc['file_key'], media_type=doc['mime'], filename=doc['name'], content_disposition_type='attachment' if download else 'inline')


@app.delete('/api/documents/{document_id}')
def delete_document(document_id: int, user=Depends(current_user)):
    with transaction() as conn:
        doc = record(conn, 'documents', document_id, user, ('property', 'manager'))
        # Sharing is access metadata and is removed with the file. Evidence used by
        # financial, maintenance, inspection or compliance records stays immutable.
        references = []
        for item in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
            table = item['name']
            if table in ('documents', 'shared_documents') or not table.replace('_', '').isalnum():
                continue
            for fk in conn.execute(f'PRAGMA foreign_key_list("{table}")').fetchall():
                if fk['table'] == 'documents' and fk['from']:
                    column = fk['from']
                    if conn.execute(f'SELECT 1 FROM "{table}" WHERE "{column}"=? LIMIT 1', (document_id,)).fetchone():
                        references.append(table)
                        break
        if references:
            raise HTTPException(409, 'This document is attached to a saved record and cannot be removed. Revoke tenant sharing instead.')
        conn.execute('DELETE FROM shared_documents WHERE document_id=?', (document_id,))
        conn.execute('DELETE FROM documents WHERE id=? AND company_id=?', (document_id, doc['company_id']))
        audit(conn, user['id'], doc['company_id'], 'deleted', 'document', document_id, {'name': doc['name'], 'sha256': doc['sha256']})
    (db.DATA_DIR / 'uploads' / doc['file_key']).unlink(missing_ok=True)
    return {'removed': True}


@app.post('/api/documents/{document_id}/share')
def share_document_with_tenant(document_id: int, data: dict, user=Depends(current_user)):
    with transaction() as conn:
        doc = record(conn, 'documents', document_id, user, ('property', 'manager'))
        if doc['kind'] in ('payment proof', 'supplier invoice', 'deposit evidence', 'Contractor agreement'):
            raise HTTPException(422, 'This document category cannot be shared with tenants.')
        contract_id = integer(data, 'contract_id')
        contract = linked(conn, 'contracts', contract_id, doc['company_id'])
        if doc['contract_id'] is not None and doc['contract_id'] != contract_id:
            raise HTTPException(422, 'This document is linked to a different tenancy.')
        from .portal_access import notify_tenant, share_document
        share_document(conn, doc['company_id'], contract_id, document_id, user['id'])
        notify_tenant(conn, contract_id, 'Document shared with you', f'{doc["name"]} is available in Documents.', 'documents')
        audit(conn, user['id'], doc['company_id'], 'shared', 'document', document_id, {'contract_id': contract_id})
    return {'shared': True}


@app.post('/api/documents/{document_id}/revoke-sharing')
def revoke_document_sharing(document_id: int, user=Depends(current_user)):
    with transaction() as conn:
        doc = record(conn, 'documents', document_id, user, ('property', 'manager'))
        links = conn.execute('SELECT contract_id FROM shared_documents WHERE document_id=? AND company_id=? AND revoked=0', (document_id, doc['company_id'])).fetchall()
        if not links:
            raise HTTPException(409, 'This document is not currently shared with a tenant.')
        conn.execute('UPDATE shared_documents SET revoked=1 WHERE document_id=? AND company_id=? AND revoked=0', (document_id, doc['company_id']))
        from .portal_access import notify_tenant
        for link in links:
            notify_tenant(conn, link['contract_id'], 'Document access updated', f'Tenant access to {doc["name"]} was removed.', 'documents')
        audit(conn, user['id'], doc['company_id'], 'sharing_revoked', 'document', document_id, {'contracts': [link['contract_id'] for link in links]})
    return {'revoked': len(links)}


@app.post('/api/contracts/manual')
def create_manually_signed_contract(data: dict, user=Depends(current_user)):
    """Record a tenancy after the parties have signed a PDF outside Haven."""
    company_id = integer(data, 'company_id')
    prop_id, tenant_id = integer(data, 'property_id'), integer(data, 'tenant_id')
    if data.get('confirm_signed') is not True:
        raise HTTPException(422, 'Confirm that both parties have signed the uploaded agreement.')
    signed_on, start = date_field(data, 'signed_on'), date_field(data, 'start_date')
    end = date_field(data, 'end_date') if data.get('end_date') else None
    if end and end < start:
        raise HTTPException(422, 'End date must be on or after the tenancy start date.')
    with transaction() as conn:
        authorize(conn, user, company_id, ('property', 'manager'))
        prop = linked(conn, 'properties', prop_id, company_id)
        tenant = linked(conn, 'tenants', tenant_id, company_id)
        doc = linked(conn, 'documents', integer(data, 'document_id'), company_id)
        if doc['kind'].casefold() != 'contract' or doc['mime'] != 'application/pdf':
            raise HTTPException(422, 'Upload the signed agreement as a Contract PDF first.')
        if doc['contract_id'] is not None:
            source = linked(conn, 'contracts', doc['contract_id'], company_id)
            if source['tenant_id'] != tenant_id:
                raise HTTPException(422, 'This signed PDF is linked to a different tenant.')
        overlapping = conn.execute('SELECT * FROM contracts WHERE property_id=? AND start_date<=? AND (end_date IS NULL OR end_date>=?)', (prop_id, end or '9999-12-31', start)).fetchone()
        if overlapping and overlapping['tenant_id'] == tenant_id and overlapping['status'] in ('active', 'notice given'):
            from .portal_access import notify_tenant, share_document
            share_document(conn, company_id, overlapping['id'], doc['id'], user['id'])
            notify_tenant(conn, overlapping['id'], 'Signed rental agreement available', 'Your signed rental agreement is available in Documents.', 'documents')
            audit(conn, user['id'], company_id, 'agreement_attached', 'contracts', overlapping['id'], {'document_id': doc['id'], 'signed_on': signed_on})
            return {'id': overlapping['id'], 'status': overlapping['status'], 'document_id': doc['id'], 'existing': True}
        if overlapping:
            raise HTTPException(409, 'This unit already has a tenancy during those dates.')
        if signed_on > start:
            raise HTTPException(422, 'The signing date must be on or before the tenancy start date.')
        contract_id = insert(conn, 'contracts', {'company_id': company_id, 'property_id': prop_id, 'tenant_id': tenant_id, 'signed_on': signed_on, 'start_date': start, 'end_date': end, 'status': 'active', 'deposit_cents': money(data, 'deposit', True), 'created_at': now()})
        insert(conn, 'rent_versions', {'company_id': company_id, 'contract_id': contract_id, 'effective_from': start, 'basic_cents': money(data, 'basic_rent'), 'service_cents': money(data, 'service_charge', True), 'created_at': now(), 'created_by': user['id']})
        from .portal_access import notify_tenant, share_document
        share_document(conn, company_id, contract_id, doc['id'], user['id'])
        task(conn, company_id, contract_id, 'Verify signed contract, WWS report and letting documents', start, 'Contract')
        task(conn, company_id, contract_id, 'Complete move-in inspection', start, 'Inspection')
        notify_tenant(conn, contract_id, 'Signed rental agreement available', 'Your signed rental agreement is available in Documents.', 'documents')
        audit(conn, user['id'], company_id, 'created', 'manually_signed_contract', contract_id, {'property_id': prop_id, 'tenant_id': tenant_id, 'document_id': doc['id'], 'signed_on': signed_on})
    return {'id': contract_id, 'status': 'active', 'document_id': doc['id']}


def submit_proof(conn, inv, data, upload, user_id):
    amount = money(data, 'amount')
    paid_on = date_field(data, 'paid_on')
    if paid_on > local_today().isoformat():
        raise HTTPException(422, 'Payment date cannot be in the future.')
    method = choice(data, 'method', ('Bank transfer', 'Mobile payment', 'Cash', 'Other'))
    reference = str(data.get('reference', '')).strip()[:150]
    balance = invoice_balance(conn, inv['id'])
    if amount > balance or balance <= 0:
        raise HTTPException(409, 'The submitted amount exceeds the outstanding balance. Contact finance about overpayments.')
    digest = hashlib.sha256(upload[0]).hexdigest()
    duplicate = conn.execute('SELECT p.id FROM payment_proofs p JOIN documents d ON d.id=p.document_id WHERE p.company_id=? AND p.status IN ("pending","approved") AND (d.sha256=? OR (?<>"" AND LOWER(p.reference)=LOWER(?)))', (inv['company_id'], digest, reference, reference)).fetchone()
    if duplicate:
        raise HTTPException(409, 'This receipt or transaction reference is already submitted. Contact finance if it covers several invoices.')
    pending = conn.execute('SELECT COUNT(*) FROM payment_proofs WHERE invoice_id=? AND status="pending"', (inv['id'],)).fetchone()[0]
    if pending >= 5:
        raise HTTPException(429, 'Please wait for finance to review your existing submissions.')
    doc_id = save_document(conn, inv['company_id'], inv['contract_id'], user_id, 'payment proof', upload)
    proof_id = insert(conn, 'payment_proofs', {'company_id': inv['company_id'], 'invoice_id': inv['id'], 'document_id': doc_id, 'amount_cents': amount, 'paid_on': paid_on, 'method': method, 'reference': reference, 'note': str(data.get('note', ''))[:2000], 'submitted_by': user_id, 'created_at': now()})
    audit(conn, user_id, inv['company_id'], 'submitted', 'payment_proof', proof_id, {'invoice_id': inv['id'], 'amount_cents': amount})
    return {'id': proof_id, 'status': 'pending'}


@app.post('/api/public/{token}/proof')
async def public_proof(token: str, amount: str = Form(...), paid_on: str = Form(...), method: str = Form(...), reference: str = Form(''), note: str = Form(''), file: UploadFile = File(...)):
    with connect() as conn:
        public_invoice(conn, token)
    upload = await read_upload(file)
    with transaction() as conn:
        inv = public_invoice(conn, token)
        return submit_proof(conn, inv, {'amount': amount, 'paid_on': paid_on, 'method': method, 'reference': reference, 'note': note}, upload, None)


@app.post('/api/invoices/{invoice_id}/proof')
async def staff_proof(invoice_id: int, amount: str = Form(...), paid_on: str = Form(...), method: str = Form(...), reference: str = Form(''), note: str = Form(''), file: UploadFile = File(...), user=Depends(current_user)):
    with connect() as conn:
        record(conn, 'invoices', invoice_id, user, FINANCE)
    upload = await read_upload(file)
    with transaction() as conn:
        inv = record(conn, 'invoices', invoice_id, user, FINANCE)
        return submit_proof(conn, inv, {'amount': amount, 'paid_on': paid_on, 'method': method, 'reference': reference, 'note': note}, upload, user['id'])


@app.post('/api/proofs/{proof_id}/review')
def review_proof(proof_id: int, data: dict, user=Depends(current_user)):
    decision = choice(data, 'decision', ('approved', 'rejected'))
    note = text_field(data, 'note', maximum=2000)
    with transaction() as conn:
        proof = record(conn, 'payment_proofs', proof_id, user, FINANCE)
        if proof['status'] != 'pending':
            raise HTTPException(409, 'This submission has already been reviewed.')
        if proof['submitted_by'] == user['id']:
            raise HTTPException(403, 'A different staff member must review a staff-submitted payment.')
        if decision == 'approved':
            if data.get('receipt_confirmed') is not True:
                raise HTTPException(422, 'Confirm that the funds were actually received before approving.')
            inv = linked(conn, 'invoices', proof['invoice_id'], proof['company_id'])
            if proof['amount_cents'] > invoice_balance(conn, inv['id']):
                raise HTTPException(409, 'The balance has changed. This payment exceeds the remaining amount.')
            insert(conn, 'ledger', {'company_id': proof['company_id'], 'contract_id': inv['contract_id'], 'invoice_id': inv['id'], 'proof_id': proof_id, 'kind': 'payment', 'amount_cents': -proof['amount_cents'], 'created_by': user['id'], 'created_at': now(), 'note': note})
        conn.execute('UPDATE payment_proofs SET status=?,reviewed_by=?,review_note=?,reviewed_at=? WHERE id=?', (decision, user['id'], note, now(), proof_id))
        audit(conn, user['id'], proof['company_id'], decision, 'payment_proof', proof_id, {'note': note, 'amount_cents': proof['amount_cents']})
        from .portal_access import notify_tenant
        contract_id = conn.execute('SELECT contract_id FROM invoices WHERE id=?', (proof['invoice_id'],)).fetchone()[0]
        notify_tenant(conn, contract_id, 'Payment ' + decision, note, 'payments')
    return {'ok': True}


@app.post('/api/proofs/{proof_id}/reverse')
def reverse_payment(proof_id: int, data: dict, user=Depends(current_user)):
    note = text_field(data, 'note')
    with transaction() as conn:
        proof = record(conn, 'payment_proofs', proof_id, user, ('manager',))
        if proof['status'] != 'approved':
            raise HTTPException(409, 'Only an approved payment can be reversed.')
        posting = conn.execute('SELECT * FROM ledger WHERE proof_id=? AND kind="payment"', (proof_id,)).fetchone()
        insert(conn, 'ledger', {'company_id': proof['company_id'], 'contract_id': posting['contract_id'], 'invoice_id': posting['invoice_id'], 'kind': 'reversal', 'amount_cents': -posting['amount_cents'], 'reverses_id': posting['id'], 'created_by': user['id'], 'created_at': now(), 'note': note})
        conn.execute('UPDATE payment_proofs SET status="reversed" WHERE id=?', (proof_id,))
        audit(conn, user['id'], proof['company_id'], 'reversed', 'payment_proof', proof_id, {'note': note})
        from .portal_access import notify_tenant
        notify_tenant(conn, posting['contract_id'], 'Payment correction', note, 'payments')
    return {'ok': True}


@app.post('/api/tasks/{task_id}/status')
def task_status(task_id: int, data: dict, user=Depends(current_user)):
    status = choice(data, 'status', ('open', 'done'))
    with transaction() as conn:
        row = record(conn, 'tasks', task_id, user, ('property','finance','manager'))
        conn.execute('UPDATE tasks SET status=? WHERE id=?', (status, task_id))
        audit(conn, user['id'], row['company_id'], status, 'task', task_id, {})
    return {'ok': True}


@app.post('/api/repairs/{repair_id}/status')
def repair_status(repair_id: int, data: dict, user=Depends(current_user)):
    status = choice(data, 'status', ('received','triaged','planned','in progress','waiting','done','closed','reopened'))
    with transaction() as conn:
        row = record(conn, 'repairs', repair_id, user, OPERATIONS)
        doc_id, note = None, None
        if status in ('done', 'closed'):
            doc_id = integer(data, 'document_id')
            linked(conn, 'documents', doc_id, row['company_id'])
            note = text_field(data, 'note')
        conn.execute('UPDATE repairs SET status=?,completion_note=?,completion_document_id=?,completed_at=? WHERE id=?', (status, note, doc_id, now() if doc_id else None, repair_id))
        if status != 'received':
            sla.mark_responded(conn, repair_id)
        audit(conn, user['id'], row['company_id'], 'status_changed', 'repair', repair_id, {'from': row['status'], 'to': status, 'note': note})
        from .portal_access import notify_tenant, share_document
        insert(conn, 'repair_updates', {'company_id': row['company_id'], 'repair_id': repair_id, 'user_id': user['id'], 'message': 'Status: ' + status + ('. ' + note if note else ''), 'document_id': doc_id, 'tenant_visible': 1, 'created_at': now()})
        if doc_id:
            document = linked(conn, 'documents', doc_id, row['company_id'])
            if document['contract_id'] and document['contract_id'] != row['contract_id']:
                raise HTTPException(422, 'Completion evidence belongs to another tenancy.')
            share_document(conn, row['company_id'], row['contract_id'], doc_id, user['id'])
        notify_tenant(conn, row['contract_id'], 'Repair status updated', row['title'] + ': ' + status, 'repairs')
    return {'ok': True}


@app.post('/api/contracts/{contract_id}/end')
def end_contract(contract_id: int, data: dict, user=Depends(current_user)):
    end = date_field(data, 'end_date')
    reason = text_field(data, 'reason')
    with transaction() as conn:
        c = record(conn, 'contracts', contract_id, user, OPERATIONS)
        if c['status'] == 'ended':
            raise HTTPException(409, 'This tenancy has already ended.')
        if end < c['start_date']:
            raise HTTPException(422, 'End date cannot precede the start date.')
        invoiced = conn.execute('SELECT period FROM invoices WHERE contract_id=? ORDER BY period DESC LIMIT 1', (contract_id,)).fetchone()
        if invoiced and invoiced['period'] >= end[:7] and (end[-2:] != str(calendar.monthrange(int(end[:4]), int(end[5:7]))[1]) or invoiced['period'] > end[:7]):
            raise HTTPException(409, 'Issued invoices cover this end date. Finance must resolve the final-period adjustment outside this release before termination can be recorded.')
        conn.execute('UPDATE contracts SET end_date=?,status=? WHERE id=?', (end, 'ended' if end <= local_today().isoformat() else 'notice given', contract_id))
        task(conn, c['company_id'], contract_id, 'Complete move-out inspection', end, 'Inspection')
        task(conn, c['company_id'], contract_id, 'Review and return deposit', (date.fromisoformat(end)+timedelta(days=14)).isoformat(), 'Deposit', 'Finance team')
        task(conn, c['company_id'], contract_id, 'Prepare final annual service-charge statement', f'{int(end[:4])+1}-06-30', 'Service charges', 'Finance team')
        audit(conn, user['id'], c['company_id'], 'termination_recorded', 'contract', contract_id, {'end_date': end, 'reason': reason})
    return {'ok': True}


@app.post('/api/deposits')
def deposit_event(data: dict, user=Depends(current_user)):
    kind = choice(data, 'kind', ('received', 'deduction', 'refund'))
    amount = money(data, 'amount')
    reason = text_field(data, 'reason')
    with transaction() as conn:
        c = record(conn, 'contracts', integer(data, 'contract_id'), user, FINANCE)
        doc_id = integer(data, 'document_id')
        linked(conn, 'documents', doc_id, c['company_id'])
        held = deposit_balance(conn, c['id'])
        if kind != 'received' and amount > held:
            raise HTTPException(409, 'The amount exceeds the deposit currently held.')
        if kind == 'received' and amount+held > c['deposit_cents']:
            raise HTTPException(409, 'The receipt exceeds the agreed deposit.')
        if kind == 'deduction':
            choice(data, 'deduction_type', ('Unpaid rent', 'Unpaid service charges', 'Tenant damage'))
            reason = data['deduction_type'] + ': ' + reason
        eid = insert(conn, 'deposit_events', {'company_id': c['company_id'], 'contract_id': c['id'], 'kind': kind, 'amount_cents': amount, 'status': 'approved' if kind == 'received' else 'pending', 'reason': reason, 'document_id': doc_id, 'created_by': user['id'], 'created_at': now()})
        if kind == 'deduction' and c['end_date']:
            due = (date.fromisoformat(c['end_date'])+timedelta(days=30)).isoformat()
            conn.execute('UPDATE tasks SET due_date=? WHERE contract_id=? AND category="Deposit" AND status="open"', (due, c['id']))
        audit(conn, user['id'], c['company_id'], 'recorded', 'deposit_event', eid, {'kind': kind, 'amount_cents': amount})
    return {'id': eid}


def deposit_balance(conn, contract_id):
    return conn.execute('SELECT COALESCE(SUM(CASE WHEN kind="received" THEN amount_cents ELSE -amount_cents END),0) FROM deposit_events WHERE contract_id=? AND status="approved"', (contract_id,)).fetchone()[0]


@app.post('/api/deposits/{event_id}/review')
def deposit_review(event_id: int, data: dict, user=Depends(current_user)):
    decision = choice(data, 'decision', ('approved','rejected'))
    note = text_field(data, 'note')
    with transaction() as conn:
        ev = record(conn, 'deposit_events', event_id, user, ('manager',))
        if ev['status'] != 'pending':
            raise HTTPException(409, 'This item has already been reviewed.')
        if ev['created_by'] == user['id']:
            raise HTTPException(403, 'A different person must approve a deposit deduction or refund.')
        if decision == 'approved' and ev['amount_cents'] > deposit_balance(conn, ev['contract_id']):
            raise HTTPException(409, 'Insufficient deposit balance.')
        if ev['kind'] == 'refund' and decision == 'approved' and data.get('receipt_confirmed') is not True:
            raise HTTPException(422, 'Confirm that the refund was paid externally.')
        conn.execute('UPDATE deposit_events SET status=?,approved_by=? WHERE id=?', (decision, user['id'], event_id))
        audit(conn, user['id'], ev['company_id'], decision, 'deposit_event', event_id, {'note': note})
    return {'ok': True}


@app.post('/api/inspections/{inspection_id}/finalize')
def finalize_inspection(inspection_id: int, data: dict, user=Depends(current_user)):
    name = text_field(data, 'signature_name', maximum=200)
    with transaction() as conn:
        row = record(conn, 'inspections', inspection_id, user, ('inspector','property','finance','manager'))
        if row['status'] != 'draft':
            raise HTTPException(409, 'This inspection is already frozen.')
        if not row['document_id']:
            raise HTTPException(422, 'Create an inspection with its signed report attached before finalizing.')
        conn.execute('UPDATE inspections SET status="final",signature_name=? WHERE id=?', (name, inspection_id))
        audit(conn, user['id'], row['company_id'], 'finalized', 'inspection', inspection_id, {'attested_by': name})
        from .portal_access import notify_tenant, share_document
        doc = linked(conn, 'documents', row['document_id'], row['company_id'])
        if doc['contract_id'] and doc['contract_id'] != row['contract_id']:
            raise HTTPException(422, 'The report belongs to another tenancy.')
        share_document(conn, row['company_id'], row['contract_id'], row['document_id'], user['id'])
        notify_tenant(conn, row['contract_id'], 'Inspection report available', row['kind'] + ' inspection has been finalized.', 'documents')
    return {'ok': True}


@app.post('/api/rent-indexations/propose')
def propose_rent_indexation(data:dict,user=Depends(current_user)):
    company_id=integer(data,'company_id')
    contract_id=integer(data,'contract_id')
    effective=date_field(data,'effective_from')
    cpi=None
    if data.get('cpi_period'):
        with connect() as conn:
            authorize(conn,user,company_id,FINANCE)
        try:
            cpi=cpi_annual_rate(str(data['cpi_period']))
            percentage=Decimal(str(cpi['percentage']))
        except CBSDataError as exc:
            raise HTTPException(422,str(exc))
    else:
        try:
            percentage=Decimal(str(data.get('percentage','')))
        except (InvalidOperation,ValueError,TypeError):
            raise HTTPException(422,'Enter an indexation percentage between -50% and 100%.')
    if not percentage.is_finite() or percentage < -50 or percentage > 100:
        raise HTTPException(422,'Enter an indexation percentage between -50% and 100%.')
    with transaction() as conn:
        authorize(conn,user,company_id,FINANCE)
        contract=linked(conn,'contracts',contract_id,company_id)
        if effective<=local_today().isoformat() or (contract['end_date'] and effective>contract['end_date']):
            raise HTTPException(422,'Choose a future effective date within the tenancy.')
        current=conn.execute('SELECT * FROM rent_versions WHERE contract_id=? AND effective_from<=? ORDER BY effective_from DESC LIMIT 1',(contract_id,effective)).fetchone()
        if not current:
            raise HTTPException(409,'No rent version is in effect for this tenancy on the selected date.')
        if conn.execute('SELECT id FROM invoices WHERE contract_id=? AND period>=?',(contract_id,effective[:7])).fetchone():
            raise HTTPException(409,'Invoices already exist for the proposed effective period.')
        new_basic=int((Decimal(current['basic_cents'])*(Decimal(100)+percentage)/Decimal(100)).quantize(Decimal('1'),rounding=ROUND_HALF_UP))
        if new_basic<=0:
            raise HTTPException(422,'The calculated basic rent must remain above zero.')
        pct=f'{percentage.normalize():f}'
        note=text_field(data,'note',maximum=1000) if str(data.get('note','')).strip() else ''
        reason=f'Landlord-entered rent indexation: {pct}%'+(f' — {note}' if note else '')
        if cpi:
            reason=f'CBS CPI year-on-year for {cpi["period"]}: {pct}%'+(f' - {note}' if note else '')
            source=f'CBS StatLine table {cpi["table"]}; CPI all items index {cpi["index"]}; annual percentage change for {cpi["period"]}. Retrieved {local_today().isoformat()}. Contract formula and statutory limits require independent review. Source: {cpi["source_url"]}'
        else:
            source='Manually entered percentage; calculation is a proposal and has not been checked against statutory limits.'
        proposal_id=insert(conn,'rent_proposals',{'company_id':company_id,'contract_id':contract_id,'effective_from':effective,'basic_cents':new_basic,'service_cents':current['service_cents'],'reason':reason,'source':source,'created_by':user['id'],'created_at':now()})
        audit(conn,user['id'],company_id,'indexation_proposed','rent_proposal',proposal_id,{'contract_id':contract_id,'effective_from':effective,'percentage':pct,'old_basic_cents':current['basic_cents'],'new_basic_cents':new_basic,'cpi_period':cpi['period'] if cpi else None,'source':cpi['source_url'] if cpi else 'manual'})
    return {'id':proposal_id,'percentage':pct,'old_basic_cents':current['basic_cents'],'basic_cents':new_basic,'service_cents':current['service_cents'],'effective_from':effective,'status':'pending','cbs':cpi}


@app.get('/api/rent-indexations/cbs')
def lookup_cbs_cpi(company_id:int,period:str,user=Depends(current_user)):
    with connect() as conn:
        authorize(conn,user,company_id,FINANCE)
    try:
        return cpi_annual_rate(period)
    except CBSDataError as exc:
        raise HTTPException(422,str(exc))


@app.post('/api/rent-proposals/{proposal_id}/review')
def review_rent(proposal_id: int, data: dict, user=Depends(current_user)):
    decision = choice(data, 'decision', ('approved','rejected'))
    note = text_field(data, 'note')
    with transaction() as conn:
        p = record(conn, 'rent_proposals', proposal_id, user, ('manager',))
        role = authorize(conn, user, p['company_id'], ('manager',))
        if p['status'] != 'pending':
            raise HTTPException(409, 'This proposal has already been reviewed.')
        admin_self_review = p['created_by'] == user['id'] and role == 'admin'
        if p['created_by'] == user['id'] and not admin_self_review:
            raise HTTPException(403, 'A different person must approve a rent change.')
        if admin_self_review and decision == 'approved' and data.get('receipt_confirmed') is not True:
            raise HTTPException(422, 'An administrator approving their own proposal must attest that they checked the contract, legal limits and notice requirements.')
        if decision == 'approved':
            if data.get('receipt_confirmed') is not True:
                raise HTTPException(422, 'Confirm that the clause, legal limits, valuation and notice requirements were checked.')
            if p['effective_from'] <= local_today().isoformat():
                raise HTTPException(409, 'The proposed effective date has passed. Create a new proposal.')
            if conn.execute('SELECT id FROM invoices WHERE contract_id=? AND period>=?', (p['contract_id'], p['effective_from'][:7])).fetchone():
                raise HTTPException(409, 'Invoices have already been issued for this effective period.')
            insert(conn, 'rent_versions', {'company_id': p['company_id'], 'contract_id': p['contract_id'], 'effective_from': p['effective_from'], 'basic_cents': p['basic_cents'], 'service_cents': p['service_cents'], 'created_at': now(), 'created_by': user['id']})
        conn.execute('UPDATE rent_proposals SET status=?,approved_by=? WHERE id=?', (decision, user['id'], proposal_id))
        audit(conn, user['id'], p['company_id'], decision, 'rent_proposal', proposal_id, {'note': note, 'admin_self_review': admin_self_review})
    return {'ok': True}


@app.delete('/api/rent-proposals/{proposal_id}')
def remove_rejected_rent_proposal(proposal_id: int, user=Depends(current_user)):
    with transaction() as conn:
        proposal = record(conn, 'rent_proposals', proposal_id, user, ('manager','finance'))
        role = authorize(conn, user, proposal['company_id'], ('manager','finance'))
        if proposal['status'] != 'rejected':
            raise HTTPException(409, 'Only rejected rent proposals can be removed.')
        if role not in ('admin','manager') and proposal['created_by'] != user['id']:
            raise HTTPException(403, 'Only the proposal creator or a manager can remove this rejected proposal.')
        conn.execute('DELETE FROM rent_proposals WHERE id=?', (proposal_id,))
        audit(conn, user['id'], proposal['company_id'], 'removed', 'rent_proposal', proposal_id, {'previous_status':'rejected','reason':proposal['reason']})
    return {'ok':True}


@app.post('/api/service-charge-groups')
def create_service_charge_group(data: dict, user=Depends(current_user)):
    company_id = integer(data, 'company_id')
    name = text_field(data, 'name', maximum=120)
    raw_ids = data.get('property_ids')
    if not isinstance(raw_ids, list) or len(raw_ids) < 2:
        raise HTTPException(422, 'Select at least two units for a shared building group.')
    try:
        property_ids = list(dict.fromkeys(int(x) for x in raw_ids))
    except (ValueError, TypeError):
        raise HTTPException(422, 'Choose valid units.')
    if len(property_ids) < 2:
        raise HTTPException(422, 'Select at least two different units.')
    raw_shares = data.get('shares') or {}
    if not isinstance(raw_shares, dict):
        raise HTTPException(422, 'Agreed shares must be supplied as unit percentages.')
    with transaction() as conn:
        authorize(conn, user, company_id, FINANCE)
        marks = ','.join('?' for _ in property_ids)
        members = conn.execute(f'SELECT id FROM properties WHERE company_id=? AND id IN ({marks})', (company_id, *property_ids)).fetchall()
        if len(members) != len(property_ids):
            raise HTTPException(422, 'All selected units must belong to this portfolio.')
        gid = insert(conn, 'service_charge_groups', {'company_id': company_id, 'name': name, 'created_by': user['id'], 'created_at': now()})
        for pid in property_ids:
            try:
                share = float(raw_shares.get(str(pid), 0) or 0)
                if not 0 <= share <= 100:
                    raise ValueError()
            except (ValueError, TypeError):
                raise HTTPException(422, 'Each agreed share must be from 0 to 100%.')
            conn.execute('INSERT INTO service_charge_group_units(group_id,property_id,share_percent) VALUES(?,?,?)', (gid, pid, share))
        audit(conn, user['id'], company_id, 'created', 'service_charge_group', gid, {'name': name, 'property_ids': property_ids})
    return {'id': gid}


@app.post('/api/settlements')
def create_settlement(data: dict, user=Depends(current_user)):
    year = integer(data, 'year')
    if not 2000 <= year < local_today().year:
        raise HTTPException(422, 'Choose a completed calendar year.')
    with transaction() as conn:
        group_id = integer(data, 'service_group_id') if data.get('service_group_id') else None
        if group_id:
            group = conn.execute('SELECT * FROM service_charge_groups WHERE id=?', (group_id,)).fetchone()
            if not group:
                raise HTTPException(404, 'Building group not found.')
            authorize(conn, user, group['company_id'], FINANCE)
            prop = conn.execute('SELECT p.* FROM properties p JOIN service_charge_group_units u ON u.property_id=p.id WHERE u.group_id=? ORDER BY p.id LIMIT 1', (group_id,)).fetchone()
            members = conn.execute('SELECT p.*,u.share_percent FROM properties p JOIN service_charge_group_units u ON u.property_id=p.id WHERE u.group_id=? ORDER BY p.id', (group_id,)).fetchall()
            costs = [dict(r) for r in conn.execute('SELECT * FROM cost_lines WHERE service_group_id=? AND year=?', (group_id, year))]
            if conn.execute('SELECT id FROM settlements WHERE service_group_id=? AND year=?', (group_id, year)).fetchone():
                raise HTTPException(409, 'A settlement already exists for this building group and year.')
        else:
            prop = record(conn, 'properties', integer(data, 'property_id'), user, FINANCE)
            members = [prop]
            costs = [dict(r) for r in conn.execute('SELECT * FROM cost_lines WHERE property_id=? AND service_group_id IS NULL AND year=?', (prop['id'], year))]
            if conn.execute('SELECT id FROM settlements WHERE property_id=? AND year=? AND service_group_id IS NULL', (prop['id'], year)).fetchone():
                raise HTTPException(409, 'A settlement already exists for this property and year.')
        if not costs:
            raise HTTPException(422, 'Add invoice-backed costs for this property or building group and year first.')
        days_year = 366 if calendar.isleap(year) else 365
        property_ids = [m['id'] for m in members]
        marks = ','.join('?' for _ in property_ids)
        contracts = conn.execute(f'''SELECT c.*,t.name FROM contracts c JOIN tenants t ON t.id=c.tenant_id
            WHERE c.property_id IN ({marks}) AND c.start_date<=? AND (c.end_date IS NULL OR c.end_date>=?) ORDER BY c.id''', (*property_ids, f'{year}-12-31', f'{year}-01-01')).fetchall()
        days_by_contract = {}
        for c in contracts:
            start = max(date(year,1,1), date.fromisoformat(c['start_date']))
            end = min(date(year,12,31), date.fromisoformat(c['end_date'])) if c['end_date'] else date(year,12,31)
            days_by_contract[c['id']] = {'contract': c, 'days': (end-start).days+1}
        lines_by_id = {}
        for c in contracts:
            lines_by_id[c['id']] = {'contract_id':c['id'],'tenant':c['name'],'property_id':c['property_id'],'days':days_by_contract[c['id']]['days'],'allocated_cents':0,'advance_cents':0,'result_cents':0,'costs':[]}
        total = sum(c['amount_cents'] for c in costs)
        for cost in costs:
            scope_members = members if cost.get('service_group_id') else [m for m in members if m['id']==cost['property_id']]
            method = cost.get('allocation_method') or 'tenancy_days'
            if method == 'equal':
                weights = {m['id']: Decimal(1)/Decimal(len(scope_members)) for m in scope_members}
            elif method == 'floor_area':
                area_total = sum(Decimal(str(m['area'])) for m in scope_members)
                if area_total <= 0 or any(Decimal(str(m['area'])) <= 0 for m in scope_members):
                    raise HTTPException(422, 'Enter a positive floor area for every unit in the group before using floor-area allocation.')
                weights = {m['id']:Decimal(str(m['area']))/area_total for m in scope_members}
            elif method == 'fixed_percentage':
                percentages = {m['id']:Decimal(str(m['share_percent'])) for m in scope_members}
                if sum(percentages.values()) != Decimal(100) or any(v < 0 for v in percentages.values()):
                    raise HTTPException(422, 'Agreed unit percentages must add up to exactly 100% before drafting the settlement.')
                weights = {pid:share/Decimal(100) for pid,share in percentages.items()}
            else:
                weights = {m['id']:Decimal(1) for m in scope_members}
            for pid, weight in weights.items():
                unit_contracts = [v for v in days_by_contract.values() if v['contract']['property_id']==pid]
                for item in unit_contracts:
                    c = item['contract']; occupied = item['days']
                    share = int((Decimal(cost['amount_cents'])*weight*Decimal(occupied)/Decimal(days_year)).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
                    if c['id'] not in lines_by_id:
                        continue
                    detail={'cost_id':cost['id'],'category':cost['category'],'supplier':cost['supplier'],'amount_cents':cost['amount_cents'],'allocated_cents':share,'allocation_method':method,'unit_weight':float(weight),'occupied_days':occupied,'year_days':days_year}
                    lines_by_id[c['id']]['costs'].append(detail)
                    lines_by_id[c['id']]['allocated_cents']+=share
        lines = list(lines_by_id.values())
        for line in lines:
            c = next(x for x in contracts if x['id']==line['contract_id'])
            invoices = conn.execute('SELECT * FROM invoices WHERE contract_id=? AND period LIKE ?', (c['id'], f'{year}-%')).fetchall()
            # Only verified payments contribute to paid advances, proportionally across invoice components.
            advances = 0
            for inv in invoices:
                paid = -conn.execute('SELECT COALESCE(SUM(amount_cents),0) FROM ledger WHERE invoice_id=? AND kind IN ("payment","reversal")',(inv['id'],)).fetchone()[0]
                advances += int((Decimal(paid)*inv['service_cents']/inv['total_cents']).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
            line['advance_cents']=advances
            line['result_cents']=line['allocated_cents']-advances
        if not lines:
            raise HTTPException(422, 'No tenancies overlap this year.')
        group_name = group['name'] if group_id else None
        method_names={'equal':'equally by unit','floor_area':'by floor area','fixed_percentage':'by agreed percentages','tenancy_days':'single-unit cost prorated by occupancy days'}
        method_summary=', '.join(f'{c["category"]}: {method_names.get(c.get("allocation_method"),"occupancy days")}' for c in costs)
        snapshot = {'property': group_name or prop['name'], 'service_group_id':group_id,'year': year, 'method': f'{method_summary}. Unit shares are prorated by occupancy days; vacancy share stays with the landlord. Paid advances allocated proportionally.', 'costs': costs, 'lines': lines, 'total_cents': total, 'landlord_share_cents': total-sum(x['allocated_cents'] for x in lines)}
        sid = insert(conn, 'settlements', {'company_id': prop['company_id'], 'property_id': prop['id'], 'service_group_id':group_id, 'year': year, 'snapshot': json.dumps(snapshot), 'created_by': user['id'], 'created_at': now()})
        audit(conn, user['id'], prop['company_id'], 'drafted', 'settlement', sid, {'year': year,'service_group_id':group_id})
    return {'id': sid}


@app.post('/api/settlements/{settlement_id}/approve')
def approve_settlement(settlement_id: int, data: dict, user=Depends(current_user)):
    note = text_field(data, 'note')
    with transaction() as conn:
        s = record(conn, 'settlements', settlement_id, user, ('manager', 'admin'))
        if s['status'] != 'draft':
            raise HTTPException(409, 'The settlement has already been approved.')
        approver_role = authorize(conn, user, s['company_id'], ('manager', 'admin'))
        if s['created_by'] == user['id'] and approver_role != 'admin':
            raise HTTPException(403, 'A different person must approve this settlement.')
        if data.get('receipt_confirmed') is not True:
            raise HTTPException(422, 'Confirm the cost eligibility, allocation and advance payments have been independently verified.')
        conn.execute('UPDATE settlements SET status="approved",approved_by=? WHERE id=?', (user['id'], settlement_id))
        for line in json.loads(s['snapshot'])['lines']:
            insert(conn, 'ledger', {'company_id': s['company_id'], 'contract_id': line['contract_id'], 'kind': 'service_settlement', 'amount_cents': line['result_cents'], 'created_by': user['id'], 'created_at': now(), 'note': f'Settlement #{settlement_id}, {s["year"]}'})
            task(conn, s['company_id'], line['contract_id'], f'Deliver service-charge statement #{settlement_id} and settle balance', f'{s["year"]+1}-06-30', 'Service charges', 'Finance team')
        audit(conn, user['id'], s['company_id'], 'approved', 'settlement', settlement_id, {'note': note})
    return {'ok': True}


@app.post('/api/arrears/run')
def arrears_run(data: dict, user=Depends(current_user)):
    company = integer(data, 'company_id')
    with connect() as conn:
        authorize(conn, user, company, FINANCE)
    count=process_overdue_invoices(company)
    from .email_delivery import deliver_pending_emails
    deliver_pending_emails(company_id=company)
    with transaction() as conn:
        audit(conn, user['id'], company, 'arrears_processed', 'arrears', None, {'cases_created': count})
    return {'created': count}


@app.post('/api/email-reminders/{job_id}/retry')
def retry_email_reminder(job_id: int, user=Depends(current_user)):
    with transaction() as conn:
        job = record(conn, 'outbound_email_jobs', job_id, user, FINANCE)
        company_id = job['company_id']
        authorize(conn, user, company_id, FINANCE)
        if job['status'] != 'failed':
            raise HTTPException(409, 'Only failed emails can be retried.')
        conn.execute('UPDATE outbound_email_jobs SET status="queued",attempts=0,next_attempt_at=?,last_error=NULL WHERE id=?', (now(), job_id))
        audit(conn, user['id'], company_id, 'retry_queued', 'invoice_email', job_id, {'invoice_id': job['invoice_id'], 'kind': job['kind']})
    from .email_delivery import deliver_pending_emails
    result = deliver_pending_emails(company_id=company_id)
    return {'ok': True, 'result': result}


@app.post('/api/arrears/cases/{case_id}/step')
def arrears_case_step(case_id:int,data:dict,user=Depends(current_user)):
    step=choice(data,'step',('personal_contact_attempted','debt_help_info_given','written_reminder_sent','municipality_consent_offered','tenant_objected','municipality_reported','debt_help_accepted','fourteen_day_letter_sent','court_review_decision','bailiff_handover'))
    occurred_on=date_field(data,'occurred_on',local_today().isoformat())
    if occurred_on>local_today().isoformat():
        raise HTTPException(422,'An arrears step cannot be dated in the future.')
    note=str(data.get('note','')).strip()[:2000]
    received_on=None
    if step=='fourteen_day_letter_sent':
        received_on=date_field(data,'received_on')
        if received_on>local_today().isoformat() or data.get('legal_template_confirmed') is not True:
            raise HTTPException(422,'Confirm the legally reviewed letter was sent and enter its actual receipt date.')
    if step=='municipality_reported' and (data.get('municipality_process_confirmed') is not True or data.get('municipality_consent_confirmed') is not True):
        raise HTTPException(422,'Confirm the municipality reporting arrangement and the tenant’s written permission before recording a referral.')
    with transaction() as conn:
        case=record(conn,'arrears_cases',case_id,user,FINANCE)
        role=authorize(conn,user,case['company_id'],FINANCE)
        if step in ('court_review_decision','bailiff_handover') and role!='manager':
            raise HTTPException(403,'A manager must make this escalation decision.')
        if case['status'] in ('resolved','closed'):
            raise HTTPException(409,'This arrears case is no longer open.')
        age=(local_today()-date.fromisoformat(conn.execute('SELECT due_date FROM invoices WHERE id=?',(case['invoice_id'],)).fetchone()[0])).days
        have=lambda name: bool(conn.execute('SELECT 1 FROM arrears_events WHERE case_id=? AND step=?',(case_id,name)).fetchone())
        if step=='municipality_reported':
            required=('personal_contact_attempted','debt_help_info_given','written_reminder_sent','municipality_consent_offered')
            if age<30 or not all(have(x) for x in required) or have('tenant_objected'):
                raise HTTPException(422,'A municipality report needs at least 30 days overdue, personal contact, debt-help information, a written reminder and an offer to share details; the tenant must not have objected.')
            if have('municipality_reported'):
                raise HTTPException(409,'The municipality referral is already recorded.')
        if step=='municipality_consent_offered' and not (have('personal_contact_attempted') and have('debt_help_info_given') and have('written_reminder_sent')):
            raise HTTPException(422,'Record personal contact, debt-help information and the written reminder first.')
        if step=='written_reminder_sent' and not have('personal_contact_attempted'):
            raise HTTPException(422,'Record the personal contact attempt first.')
        if step=='fourteen_day_letter_sent' and not have('written_reminder_sent'):
            raise HTTPException(422,'Record the written reminder before the 14-day letter.')
        if step=='debt_help_accepted' and not have('debt_help_info_given'):
            raise HTTPException(422,'Record that debt-help information was provided first.')
        if step=='court_review_decision' and age<90:
            raise HTTPException(422,'Manager review is scheduled at about 90 days overdue.')
        if step=='bailiff_handover' and not have('court_review_decision'):
            raise HTTPException(422,'A manager must record the court-route decision before bailiff handover.')
        if step=='tenant_objected' and have('municipality_reported'):
            raise HTTPException(409,'The municipality referral has already been recorded; contact the municipality and data-protection lead.')
        event_id=insert(conn,'arrears_events',{'company_id':case['company_id'],'case_id':case_id,'step':step,'occurred_on':occurred_on,'received_on':received_on,'note':note,'created_by':user['id'],'created_at':now()})
        if step=='debt_help_accepted':
            until=(local_today()+timedelta(days=30)).isoformat()
            conn.execute('UPDATE arrears_cases SET status="paused",paused_until=?,updated_at=? WHERE id=?',(until,now(),case_id))
            task(conn,case['company_id'],case['contract_id'],f'Resume arrears follow-up for invoice {conn.execute("SELECT number FROM invoices WHERE id=?",(case["invoice_id"],)).fetchone()[0]}',until,'Arrears','Finance team')
        if step=='fourteen_day_letter_sent':
            deadline=(date.fromisoformat(received_on)+timedelta(days=14)).isoformat()
            task(conn,case['company_id'],case['contract_id'],f'Check 14-day letter deadline for invoice {conn.execute("SELECT number FROM invoices WHERE id=?",(case["invoice_id"],)).fetchone()[0]}',deadline,'Arrears','Finance team')
        conn.execute('UPDATE arrears_cases SET updated_at=? WHERE id=?',(now(),case_id))
        audit(conn,user['id'],case['company_id'],'recorded','arrears_step',event_id,{'case_id':case_id,'step':step,'note':note})
    return {'id':event_id,'step':step}


def pdf_response(title, lines, filename):
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import HRFlowable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    from xml.sax.saxutils import escape
    stream = io.BytesIO()
    document = SimpleDocTemplate(stream, title=title, pagesize=A4, rightMargin=20*mm, leftMargin=20*mm, topMargin=22*mm, bottomMargin=19*mm)
    styles = getSampleStyleSheet()
    ink=colors.HexColor('#19372d');green=colors.HexColor('#176b55');muted=colors.HexColor('#687b74');line_color=colors.HexColor('#dfe9e3');pale=colors.HexColor('#f1f6f3')
    styles.add(ParagraphStyle(name='ReportTitle',parent=styles['Title'],fontName='Helvetica-Bold',fontSize=20,leading=25,textColor=ink,alignment=TA_CENTER,spaceAfter=0))
    styles.add(ParagraphStyle(name='ReportMeta',parent=styles['Normal'],fontSize=9,leading=14,textColor=muted))
    styles.add(ParagraphStyle(name='ReportSection',parent=styles['Heading2'],fontName='Helvetica-Bold',fontSize=11,leading=15,textColor=green,spaceBefore=10,spaceAfter=5))
    styles.add(ParagraphStyle(name='ReportBody',parent=styles['BodyText'],fontSize=9,leading=14,textColor=ink,spaceAfter=3))
    styles.add(ParagraphStyle(name='ReportCallout',parent=styles['BodyText'],fontSize=9,leading=14,textColor=ink,backColor=pale,borderColor=line_color,borderWidth=.6,borderPadding=8,spaceBefore=5,spaceAfter=8))
    story=[Table([[Paragraph('<b>HAVEN</b><br/><font size="7">PROPERTY MANAGEMENT</font>',styles['ReportMeta']),Paragraph('WORKSPACE RECORD',styles['ReportMeta'])]],colWidths=[85*mm,85*mm],style=TableStyle([('TEXTCOLOR',(0,0),(0,0),green),('ALIGN',(1,0),(1,0),'RIGHT'),('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),0),('RIGHTPADDING',(0,0),(-1,-1),0)])),Spacer(1,12),Paragraph(escape(title),styles['ReportTitle']),Spacer(1,7),HRFlowable(width='100%',thickness=1,color=line_color),Spacer(1,12)]
    section_names={'contract history','ledger','communication timeline','document index (source attachments remain in the document vault)','actual allocated costs:','your allocated cost calculation:','review summary','allocation details'}
    for index,value in enumerate(lines):
        text=str(value).strip()
        if not text:
            story.append(Spacer(1,5));continue
        if text.lower() in section_names:
            story.append(Paragraph(escape(text.rstrip(':')),styles['ReportSection']))
        elif index==0 or (index==1 and (' | ' in text or len(text)<90)):
            story.append(Paragraph(escape(text),styles['ReportMeta']));story.append(Spacer(1,4))
        elif text.lower().startswith(('internal review report','review statement and contact','the allocation basis')):
            story.append(Paragraph(escape(text),styles['ReportCallout']))
        elif ':' in text and len(text.split(':',1)[0])<52:
            label,content=text.split(':',1)
            row=Table([[Paragraph(f'<b>{escape(label.strip())}</b>',styles['ReportBody']),Paragraph(escape(content.strip()).replace('\n','<br/>'),styles['ReportBody'])]],colWidths=[48*mm,122*mm],style=TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('LINEBELOW',(0,0),(-1,-1),.35,line_color),('LEFTPADDING',(0,0),(-1,-1),5),('RIGHTPADDING',(0,0),(-1,-1),5),('TOPPADDING',(0,0),(-1,-1),4),('BOTTOMPADDING',(0,0),(-1,-1),4)]))
            story.append(row)
        else:
            story.append(Paragraph(escape(text).replace('\n','<br/>'),styles['ReportBody']))
    def page_chrome(canvas,doc):
        width,height=A4;canvas.saveState();canvas.setFillColor(green);canvas.rect(0,height-5*mm,width,5*mm,stroke=0,fill=1);canvas.setStrokeColor(line_color);canvas.line(20*mm,14*mm,width-20*mm,14*mm);canvas.setFont('Helvetica',8);canvas.setFillColor(muted);canvas.drawString(20*mm,8*mm,str(snap.get('company_name') or 'Haven').upper()[:50]);canvas.drawRightString(width-20*mm,8*mm,f'Page {doc.page}');canvas.restoreState()
    document.build(story,onFirstPage=page_chrome,onLaterPages=page_chrome)
    return Response(stream.getvalue(), media_type='application/pdf', headers={'Content-Disposition': f'attachment; filename="{filename}"'})


def euros(cents):
    return f'EUR {cents / 100:,.2f}'


@app.get('/api/invoices/{invoice_id}/pdf')
def invoice_pdf(invoice_id: int, download: int = 0, user=Depends(current_user)):
    with transaction() as conn:
        inv = record(conn, 'invoices', invoice_id, user, FINANCE)
        snap = json.loads(inv['snapshot'])
        audit(conn, user['id'], inv['company_id'], 'downloaded', 'invoice', invoice_id, {})
    return Response(invoice_pdf_bytes(inv, snap), media_type='application/pdf', headers={'Content-Disposition': f'{"attachment" if download else "inline"}; filename="{inv["number"]}.pdf"'})


def invoice_pdf_bytes(inv, snap):
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT, TA_RIGHT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import HRFlowable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    from xml.sax.saxutils import escape

    stream=io.BytesIO()
    document=SimpleDocTemplate(stream,pagesize=A4,rightMargin=20*mm,leftMargin=20*mm,topMargin=19*mm,bottomMargin=18*mm,title=f'Rent invoice {inv["number"]}',author=snap.get('company_name','Haven'))
    ink=colors.HexColor('#173b32');green=colors.HexColor('#176b55');muted=colors.HexColor('#687b74');line=colors.HexColor('#dce7e1');pale=colors.HexColor('#f1f6f3')
    styles=getSampleStyleSheet()
    styles.add(ParagraphStyle(name='Brand',parent=styles['Normal'],fontName='Helvetica-Bold',fontSize=18,leading=22,textColor=green))
    styles.add(ParagraphStyle(name='SmallMuted',parent=styles['Normal'],fontSize=9,leading=14,textColor=muted))
    styles.add(ParagraphStyle(name='SectionLabel',parent=styles['Normal'],fontName='Helvetica-Bold',fontSize=8,leading=12,textColor=muted,spaceAfter=4))
    styles.add(ParagraphStyle(name='InvoiceTitle',parent=styles['Title'],fontName='Helvetica-Bold',fontSize=21,leading=26,textColor=ink,alignment=TA_RIGHT,spaceAfter=0))
    styles.add(ParagraphStyle(name='Info',parent=styles['Normal'],fontSize=10,leading=15,textColor=ink))
    styles.add(ParagraphStyle(name='TotalLabel',parent=styles['Normal'],fontName='Helvetica-Bold',fontSize=11,textColor=colors.white))
    styles.add(ParagraphStyle(name='TotalAmount',parent=styles['Normal'],fontName='Helvetica-Bold',fontSize=16,textColor=colors.white,alignment=TA_RIGHT))
    esc=lambda value:escape(str(value or '')).replace('\n','<br/>')
    para=lambda value,style='Info':Paragraph(esc(value),styles[style])
    rich=lambda value,style='Info':Paragraph(value,styles[style])
    brand_cell=para(snap.get('company_name','Haven'),'Brand')
    try:
        from .invoice_tools import logo_bytes
        logo=logo_bytes(inv['company_id'])
        if logo:
            from reportlab.lib.utils import ImageReader
            from reportlab.platypus import Image
            iw,ih=ImageReader(io.BytesIO(logo)).getSize()
            scale=min(54*mm/iw,20*mm/ih)
            brand_cell=[Image(io.BytesIO(logo),width=iw*scale,height=ih*scale,hAlign='LEFT'),Spacer(1,4),para(snap.get('company_name',''),'SmallMuted')]
    except Exception:
        pass  # a broken logo must never stop an invoice from being produced
    story=[Table([[brand_cell,rich('RENT INVOICE<br/><font size="9" color="#687b74">HUURFACTUUR</font>','InvoiceTitle')]],colWidths=[88*mm,82*mm],style=TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('ALIGN',(1,0),(1,0),'RIGHT'),('LEFTPADDING',(0,0),(-1,-1),0),('RIGHTPADDING',(0,0),(-1,-1),0),('BOTTOMPADDING',(0,0),(-1,-1),7)])),para(snap.get('company_address',''),'SmallMuted'),Spacer(1,11),HRFlowable(width='100%',thickness=1,color=line),Spacer(1,14)]
    obj=snap.get('object') or ', '.join(x for x in (snap.get('property_name'),snap.get('address')) if x)
    details=f'<b>Invoice number</b>  {esc(inv["number"])}<br/><b>Invoice date</b>  {esc(snap.get("invoice_date") or str(inv["created_at"])[:10])}<br/><b>Due date</b>  {esc(inv["due_date"])}<br/><b>Billing period</b>  {esc(snap.get("from", ""))} – {esc(snap.get("to", ""))}'
    if obj: details+=f'<br/><b>Property</b>  {esc(obj)}'
    if snap.get('reference'): details+=f'<br/><b>Reference</b>  {esc(snap["reference"])}'
    invoice_info=Table([[para('BILL TO','SectionLabel'),para('INVOICE DETAILS','SectionLabel')],[rich(f'<b>{esc(snap.get("tenant_name", ""))}</b><br/>{esc(snap.get("address", ""))}'),rich(details)]],colWidths=[85*mm,85*mm],style=TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('BACKGROUND',(0,0),(-1,0),pale),('BOX',(0,0),(-1,-1),.6,line),('INNERGRID',(0,0),(-1,-1),.4,line),('LEFTPADDING',(0,0),(-1,-1),10),('RIGHTPADDING',(0,0),(-1,-1),10),('TOPPADDING',(0,0),(-1,-1),9),('BOTTOMPADDING',(0,0),(-1,-1),9)]))
    story += [invoice_info,Spacer(1,22),para('CHARGES','SectionLabel')]
    currency=lambda cents:f'€ {cents/100:,.2f}'
    items=[[para('DESCRIPTION','SectionLabel'),para('AMOUNT','SectionLabel')],[para(snap.get('basic_label') or 'Basic rent'),para(currency(inv['basic_cents']))],[para(snap.get('service_label') or 'Service charge advance'),para(currency(inv['service_cents']))]]+[[para(x.get('description','')),para(currency(x.get('amount_cents',0)))] for x in snap.get('extra_items',[])]
    item_table=Table(items,colWidths=[130*mm,40*mm],style=TableStyle([('VALIGN',(0,0),(-1,-1),'MIDDLE'),('ALIGN',(1,0),(1,-1),'RIGHT'),('BACKGROUND',(0,0),(-1,0),pale),('LINEBELOW',(0,0),(-1,0),.7,line),('LINEBELOW',(0,1),(-1,-2),.4,line),('LEFTPADDING',(0,0),(-1,-1),10),('RIGHTPADDING',(0,0),(-1,-1),10),('TOPPADDING',(0,0),(-1,-1),10),('BOTTOMPADDING',(0,0),(-1,-1),10)]))
    story += [item_table,Spacer(1,12),Table([[para('TOTAL DUE','TotalLabel'),para(currency(inv['total_cents']),'TotalAmount')]],colWidths=[110*mm,60*mm],style=TableStyle([('BACKGROUND',(0,0),(-1,-1),green),('VALIGN',(0,0),(-1,-1),'MIDDLE'),('LEFTPADDING',(0,0),(-1,-1),12),('RIGHTPADDING',(0,0),(-1,-1),12),('TOPPADDING',(0,0),(-1,-1),12),('BOTTOMPADDING',(0,0),(-1,-1),12)])),Spacer(1,19)]+([para('NOTE','SectionLabel'),para(snap['note']),Spacer(1,12)] if snap.get('note') else [])+[para('HOW TO PAY','SectionLabel'),para(snap.get('payment_instructions') or 'Please contact your property manager for payment instructions.'),Spacer(1,8),para(f'Please include invoice number {inv["number"]} with your payment. Submit your payment receipt through the tenant portal after paying.','SmallMuted')]
    def page_chrome(canvas,doc):
        canvas.saveState();width,height=A4
        canvas.setFillColor(green);canvas.rect(0,height-5*mm,width,5*mm,stroke=0,fill=1)
        canvas.setStrokeColor(line);canvas.line(20*mm,13*mm,width-20*mm,13*mm)
        canvas.setFont('Helvetica',8);canvas.setFillColor(muted);canvas.drawString(20*mm,8*mm,str(snap.get('company_name') or 'Haven').upper()[:50]);canvas.drawRightString(width-20*mm,8*mm,f'{inv["number"]}  ·  Page {doc.page}')
        canvas.restoreState()
    document.build(story,onFirstPage=page_chrome,onLaterPages=page_chrome)
    return stream.getvalue()


def service_charge_statement_pdf_bytes(company_name, tenant_name, address, year, occupied_days, year_days, allocations, cost_total, advances, balance, filename='', **details):
    from .statement_pdf import statement_pdf_bytes
    return statement_pdf_bytes(company_name, tenant_name, address, year, occupied_days, year_days, allocations, cost_total, advances, balance, **details)


@app.get('/api/settlements/{settlement_id}/pdf')
def settlement_pdf(settlement_id: int, user=Depends(current_user)):
    with transaction() as conn:
        s = record(conn, 'settlements', settlement_id, user, FINANCE)
        snapshot = json.loads(s['snapshot'])
        audit(conn, user['id'], s['company_id'], 'downloaded', 'settlement', settlement_id, {})
    lines = [f'{snapshot["property"]} | {s["year"]} | {s["status"].upper()}', snapshot['method']]
    lines += [f'{c["category"]} - {c["supplier"]}: {euros(c["amount_cents"])} (document #{c["document_id"]})' for c in snapshot['costs']]
    for tenant_line in snapshot['lines']:
        for allocation in tenant_line.get('costs', []):
            basis = f'{allocation["unit_weight"]*100:.2f}% unit share x {allocation["occupied_days"]}/{allocation["year_days"]} occupancy days'
            lines.append(f'Calculation for {tenant_line["tenant"]} / contract #{tenant_line["contract_id"]}: cost #{allocation["cost_id"]} {basis} = {euros(allocation["allocated_cents"])}')
    lines += [f'{l["tenant"]}: {l["days"]} days; cost {euros(l["allocated_cents"])}; verified advances {euros(l["advance_cents"])}; balance {euros(l["result_cents"])}' for l in snapshot['lines']]
    lines += [f'Landlord share: {euros(snapshot["landlord_share_cents"])}', 'Internal review report. Create tenant-specific statements before external distribution.']
    return pdf_response('Service-charge settlement review', lines, f'settlement-{settlement_id}.pdf')


@app.get('/api/contracts/{contract_id}/bundle')
def contract_bundle(contract_id: int, user=Depends(current_user)):
    with transaction() as conn:
        c = record(conn, 'contracts', contract_id, user, ('manager',))
        tenant = linked(conn, 'tenants', c['tenant_id'], c['company_id'])
        prop = linked(conn, 'properties', c['property_id'], c['company_id'])
        lines = [f'Tenant: {tenant["name"]}', f'Property: {prop["address"]}', f'Signed: {c["signed_on"]} | Start: {c["start_date"]} | End: {c["end_date"] or "Open"}', 'Contract history']
        for r in conn.execute('SELECT * FROM rent_versions WHERE contract_id=? ORDER BY effective_from', (contract_id,)):
            lines.append(f'{r["effective_from"]}: rent {euros(r["basic_cents"])} + advance {euros(r["service_cents"])}')
        lines.append('Ledger')
        for r in conn.execute('SELECT * FROM ledger WHERE contract_id=? ORDER BY id', (contract_id,)):
            lines.append(f'{r["created_at"]} | {r["kind"]} | {euros(r["amount_cents"])} | {r["note"]}')
        lines.append('Communication timeline')
        for r in conn.execute('SELECT * FROM communications WHERE contract_id=? ORDER BY created_at', (contract_id,)):
            lines.append(f'{r["created_at"]} | {r["channel"]} | {r["status"]} | {r["subject"]}\n{r["body"]}')
        lines.append('Document index (source attachments remain in the document vault)')
        for r in conn.execute('SELECT * FROM documents WHERE contract_id=? ORDER BY id', (contract_id,)):
            lines.append(f'#{r["id"]}: {r["name"]} | SHA-256 {r["sha256"]}')
        audit(conn, user['id'], c['company_id'], 'bundle_exported', 'contract', contract_id, {})
    return pdf_response('Tenancy record summary', lines, f'tenancy-{contract_id}.pdf')


@app.get('/api/export')
def export_csv(company_id: int, user=Depends(current_user)):
    with transaction() as conn:
        authorize(conn, user, company_id, FINANCE)
        rows = conn.execute('SELECT l.id,l.created_at,l.contract_id,l.invoice_id,l.kind,l.amount_cents,l.reverses_id,l.note FROM ledger l WHERE company_id=? ORDER BY l.id', (company_id,)).fetchall()
        audit(conn, user['id'], company_id, 'exported', 'ledger', None, {'rows': len(rows)})
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(['ID','Recorded at','Contract','Invoice','Type','Amount (cents)','Reverses entry','Note'])
    for row in rows:
        writer.writerow([("'" + value if isinstance(value, str) and value.startswith(('=','+','-','@')) else value) for value in row])
    return Response(out.getvalue(), media_type='text/csv', headers={'Content-Disposition':'attachment; filename="haven-ledger.csv"'})


@app.get('/api/audit/verify')
def verify_audit(user=Depends(current_user)):
    if not user['global_admin']:
        raise HTTPException(403, 'System administrator access required.')
    previous, count = '0'*64, 0
    with connect() as conn:
        for row in conn.execute('SELECT * FROM audit ORDER BY id'):
            expected = hashlib.sha256((previous+row['payload']).encode()).hexdigest()
            if row['previous_hash'] != previous or row['hash'] != expected:
                return {'valid': False, 'failed_at': row['id']}
            previous, count = row['hash'], count+1
    return {'valid': True, 'entries': count, 'anchor': previous, 'external_anchor': False}


from .inbox import router as inbox_router
from .assistant import router as assistant_router
from .lease_offers import router as lease_router
from .invoice_tools import router as invoice_tools_router
from .paging import router as paging_router
from .owners import router as owners_router
from .owner_docs import router as owner_docs_router
from .automation import router as automation_router
from .templates_docs import router as templates_router
from .sla import router as sla_router
from .portals import router as portal_router
from .operations import router as operations_router
from .finance_ops import router as finance_ops_router
app.include_router(portal_router)
app.include_router(sla_router)
app.include_router(owners_router)
app.include_router(inbox_router)
app.include_router(assistant_router)
app.include_router(lease_router)
app.include_router(invoice_tools_router)
app.include_router(paging_router)
app.include_router(owner_docs_router)
app.include_router(automation_router)
app.include_router(templates_router)
app.include_router(operations_router)
app.include_router(finance_ops_router)

import mimetypes
mimetypes.add_type('application/manifest+json', '.webmanifest')
app.mount('/static', StaticFiles(directory=ROOT / 'static'), name='static')


@app.get('/sw.js', include_in_schema=False)
def service_worker():
    return FileResponse(ROOT / 'static' / 'sw.js', media_type='text/javascript', headers={'Service-Worker-Allowed': '/'})


@app.get('/welcome', include_in_schema=False)
def landing():
    return FileResponse(ROOT / 'static' / 'landing.html')


@app.get('/')
@app.get('/pay/{token}')
@app.get('/activate/{token}')
@app.get('/tenant')
@app.get('/owner')
@app.get('/contractor')
def index(token: str = ''):
    return FileResponse(ROOT / 'static' / 'index.html')
