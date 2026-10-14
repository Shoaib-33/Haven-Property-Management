"""Optional SMTP delivery for rent invoices and courtesy arrears reminders."""
import os
import json
import logging
import smtplib
import ssl
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import parseaddr

from . import db
from .db import audit, now


def smtp_settings():
    host = os.environ.get('HAVEN_SMTP_HOST', '').strip()
    sender = os.environ.get('HAVEN_SMTP_FROM', '').strip()
    public_url = os.environ.get('HAVEN_PUBLIC_URL', '').strip().rstrip('/')
    if not host or not sender or not public_url:
        return None
    if parseaddr(sender)[1] != sender or '@' not in sender:
        return None
    try:
        port = int(os.environ.get('HAVEN_SMTP_PORT', '587'))
        timeout = max(5, min(60, int(os.environ.get('HAVEN_SMTP_TIMEOUT', '20'))))
    except ValueError:
        return None
    username = os.environ.get('HAVEN_SMTP_USERNAME', '').strip()
    password = os.environ.get('HAVEN_SMTP_PASSWORD', '')
    if username and not password:
        return None
    return {
        'host': host, 'port': port, 'sender': sender, 'public_url': public_url,
        'username': username, 'password': password, 'timeout': timeout,
        'starttls': os.environ.get('HAVEN_SMTP_STARTTLS', '1').strip().lower() not in ('0', 'false', 'no'),
        'ssl': os.environ.get('HAVEN_SMTP_SSL', '0').strip().lower() in ('1', 'true', 'yes'),
    }


def queue_reminder(conn, invoice, kind, recipient):
    """Queue once only when outbound email has been explicitly configured."""
    if not smtp_settings() or not recipient or parseaddr(recipient)[1] != recipient or '@' not in recipient:
        return False
    conn.execute(
        'INSERT OR IGNORE INTO outbound_email_jobs '
        '(company_id,contract_id,invoice_id,kind,recipient,status,attempts,next_attempt_at,created_at) '
        'VALUES(?,?,?,?,?,"queued",0,?,?)',
        (invoice['company_id'], invoice['contract_id'], invoice['id'], kind, recipient, now(), now()),
    )
    return True


def queue_invoice(conn, invoice, recipient):
    """Queue the newly issued invoice once, when SMTP and tenant email are configured."""
    if not smtp_settings() or not recipient or parseaddr(recipient)[1] != recipient or '@' not in recipient:
        return False
    conn.execute(
        'INSERT OR IGNORE INTO outbound_email_jobs '
        '(company_id,contract_id,invoice_id,kind,recipient,status,attempts,next_attempt_at,created_at) '
        'VALUES(?,?,?,"invoice_issued",?,"queued",0,?,?)',
        (invoice['company_id'], invoice['contract_id'], invoice['id'], recipient, now(), now()),
    )
    return True


def _reminder_body(kind, invoice, tenant_name, language, balance_cents, public_url):
    portal_url = public_url + '/tenant'
    if kind == 'overdue_followup':
        if language == 'nl':
            subject = f'Tweede vriendelijke betalingsherinnering: {invoice["number"]}'
            body = (
                f'Beste {tenant_name},\n\nVolgens onze administratie staat voor huurperiode {invoice["period"]} '
                f'nog EUR {balance_cents / 100:,.2f} open. De vervaldatum was {invoice["due_date"]}. '
                'Betaal via de met uw verhuurder afgesproken betaalmethode. Heeft u al betaald of een betalingsbewijs '
                'ingediend, dan kunt u dit bericht negeren terwijl de betaling wordt gecontroleerd.\n\n'
                f'Bekijk uw factuur in het huurdersportaal: {portal_url}\n\n'
                'Dit is een vriendelijke herinnering, geen wettelijke aanmaning. Neem bij vragen contact op met uw verhuurder.'
            )
        else:
            subject = f'Second friendly payment reminder: {invoice["number"]}'
            body = (
                f'Dear {tenant_name},\n\nOur records show EUR {balance_cents / 100:,.2f} remains due for rent period '
                f'{invoice["period"]}. The due date was {invoice["due_date"]}. Please pay using the method agreed '
                'with your landlord. If you have already paid or submitted proof, you may ignore this message while it is reviewed.\n\n'
                f'View your invoice in the tenant portal: {portal_url}\n\n'
                'This is a friendly reminder, not a formal legal notice. Contact your property manager if you have questions.'
            )
    elif language == 'nl':
        subject = f'Vriendelijke betalingsherinnering: {invoice["number"]}'
        body = (
            f'Beste {tenant_name},\n\nVolgens onze administratie staat voor huurperiode {invoice["period"]} '
            f'bij factuur {invoice["number"]} nog EUR {balance_cents / 100:,.2f} open. De vervaldatum was '
            f'{invoice["due_date"]}. Betaal via de met uw verhuurder afgesproken betaalmethode. '
            'Heeft u al betaald of een betalingsbewijs ingediend, dan kunt u dit bericht negeren terwijl de betaling wordt gecontroleerd.\n\n'
            f'Bekijk uw factuur in het huurdersportaal: {portal_url}\n\n'
            'Dit is een vriendelijke herinnering, geen wettelijke aanmaning. Neem bij vragen contact op met uw verhuurder.'
        )
    else:
        subject = f'Friendly payment reminder: {invoice["number"]}'
        body = (
            f'Dear {tenant_name},\n\nOur records show EUR {balance_cents / 100:,.2f} remains due for rent invoice '
            f'{invoice["number"]}, covering {invoice["period"]}. The due date was {invoice["due_date"]}. '
            'Please pay using the method agreed with your landlord. If you have already paid or submitted proof, '
            'you may ignore this message while it is reviewed.\n\n'
            f'View your invoice in the tenant portal: {portal_url}\n\n'
            'This is a friendly reminder, not a formal legal notice. Contact your property manager if you have questions.'
        )
    return subject, body


def _invoice_body(invoice, tenant_name, language, public_url, payment_instructions=''):
    if language == 'nl':
        return (
            f'Uw huurfactuur {invoice["number"]} voor {invoice["period"]}',
            f'Beste {tenant_name},\n\nBijgevoegd vindt u de huurfactuur {invoice["number"]} voor {invoice["period"]}. '
            f'Het totaal is EUR {invoice["total_cents"] / 100:,.2f} en de vervaldatum is {invoice["due_date"]}.\n\n'
            f'{payment_instructions or "Betaal volgens de met uw verhuurder afgesproken betaalmethode."}\n\n'
            f'U kunt uw facturen en betalingsbewijs ook bekijken in het huurdersportaal: {public_url}/tenant\n\n'
            'Met vriendelijke groet,\nUw vastgoedbeheerder'
        )
    return (
        f'Your rent invoice {invoice["number"]} for {invoice["period"]}',
        f'Dear {tenant_name},\n\nAttached is rent invoice {invoice["number"]} for {invoice["period"]}. '
        f'The total is EUR {invoice["total_cents"] / 100:,.2f}, due on {invoice["due_date"]}.\n\n'
        f'{payment_instructions or "Please pay using the method agreed with your landlord."}\n\n'
        f'You can also view invoices and submit payment proof in the tenant portal: {public_url}/tenant\n\n'
        'Kind regards,\nYour property manager'
    )


def _deliver_one(job, settings):
    with db.connect() as conn:
        row = conn.execute(
            'SELECT j.*,i.number,i.period,i.due_date,i.basic_cents,i.service_cents,i.total_cents,i.snapshot,c.tenant_id,t.name tenant_name,t.language,t.email '
            'FROM outbound_email_jobs j JOIN invoices i ON i.id=j.invoice_id '
            'JOIN contracts c ON c.id=i.contract_id JOIN tenants t ON t.id=c.tenant_id WHERE j.id=?',
            (job['id'],),
        ).fetchone()
        if not row:
            with db.transaction() as c2:
                c2.execute('UPDATE outbound_email_jobs SET status="cancelled",last_error="Invoice or tenant record missing" WHERE id=?', (job['id'],))
            return 'cancelled'
        snapshot = json.loads(row['snapshot'])
        balance = None
        if row['kind'] != 'invoice_issued':
            from .main import invoice_balance
            balance = invoice_balance(conn, row['invoice_id'])
    if balance is not None and balance <= 0:
        with db.transaction() as conn:
            conn.execute('UPDATE outbound_email_jobs SET status="cancelled",last_error=NULL WHERE id=? AND status="sending"', (job['id'],))
        return 'cancelled'

    if row['kind'] == 'invoice_issued':
        subject, body = _invoice_body(row, row['tenant_name'], row['language'], settings['public_url'], snapshot.get('payment_instructions', ''))
    else:
        subject, body = _reminder_body(row['kind'], row, row['tenant_name'], row['language'], balance, settings['public_url'])
    message = EmailMessage()
    message['From'] = settings['sender']
    message['To'] = row['recipient']
    message['Subject'] = subject
    message.set_content(body)
    if row['kind'] == 'invoice_issued':
        from .main import invoice_pdf_bytes
        message.add_attachment(invoice_pdf_bytes(row, snapshot), maintype='application', subtype='pdf', filename=row['number'] + '.pdf')
    if settings['ssl']:
        server = smtplib.SMTP_SSL(settings['host'], settings['port'], timeout=settings['timeout'], context=ssl.create_default_context())
    else:
        server = smtplib.SMTP(settings['host'], settings['port'], timeout=settings['timeout'])
    with server:
        if settings['starttls'] and not settings['ssl']:
            server.starttls(context=ssl.create_default_context())
        if settings['username']:
            server.login(settings['username'], settings['password'])
        server.send_message(message)

    return 'sent'


def deliver_pending_emails(limit=50, company_id=None):
    """Attempt due jobs; retry transient errors and stop after five attempts."""
    settings = smtp_settings()
    if not settings:
        return {'sent': 0, 'failed': 0, 'queued': 0}
    stamp = now()
    lease_until = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(timespec='seconds')
    with db.transaction() as conn:
        # Recover stale in-flight work and atomically claim jobs to prevent concurrent sends.
        conn.execute('UPDATE outbound_email_jobs SET status="queued" WHERE status="sending" AND next_attempt_at<=?', (stamp,))
        query = 'SELECT * FROM outbound_email_jobs WHERE status="queued" AND next_attempt_at<=?'
        params = [stamp]
        if company_id is not None:
            query += ' AND company_id=?'
            params.append(company_id)
        query += ' ORDER BY id LIMIT ?'
        params.append(limit)
        jobs = [dict(r) for r in conn.execute(query, params)]
        for job in jobs:
            conn.execute('UPDATE outbound_email_jobs SET status="sending",attempts=attempts+1,next_attempt_at=? WHERE id=? AND status="queued"', (lease_until, job['id']))
    sent = failed = 0
    for job in jobs:
        try:
            result = _deliver_one(job, settings)
            if result == 'cancelled':
                continue
            sent_at = now()
            with db.transaction() as conn:
                conn.execute('UPDATE outbound_email_jobs SET status="sent",sent_at=?,last_error=NULL WHERE id=? AND status="sending"', (sent_at, job['id']))
                inv = conn.execute('SELECT company_id FROM invoices WHERE id=?', (job['invoice_id'],)).fetchone()
                case = conn.execute('SELECT id FROM arrears_cases WHERE invoice_id=?', (job['invoice_id'],)).fetchone()
                if inv and case and job['kind'] != 'invoice_issued':
                    step = 'email_followup_reminder_sent' if job['kind'] == 'overdue_followup' else 'email_friendly_reminder_sent'
                    conn.execute(
                        'INSERT INTO arrears_events(company_id,case_id,step,occurred_on,note,created_at) VALUES(?,?,?,?,?,?)',
                        (inv['company_id'], case['id'], step, sent_at[:10], f'Email delivered to {job["recipient"]}.', sent_at),
                    )
                if inv:
                    audit(conn, None, inv['company_id'], 'email_sent', 'invoice_email', job['id'], {'invoice_id': job['invoice_id'], 'kind': job['kind']})
            sent += 1
        except Exception as exc:
            logging.exception('Outbound email delivery failed for job %s.', job['id'])
            attempts = job['attempts'] + 1
            is_failed = attempts >= 5
            wait_minutes = (5, 15, 60, 180)[min(attempts - 1, 3)]
            retry_at = (datetime.now(timezone.utc) + timedelta(minutes=wait_minutes)).isoformat(timespec='seconds')
            with db.transaction() as conn:
                conn.execute(
                    'UPDATE outbound_email_jobs SET status=?,attempts=?,next_attempt_at=?,last_error=? WHERE id=? AND status="sending"',
                    ('failed' if is_failed else 'queued', attempts, retry_at, str(exc)[:400], job['id']),
                )
            if is_failed:
                failed += 1
    with db.connect() as conn:
        queued = conn.execute('SELECT count(*) FROM outbound_email_jobs WHERE status="queued"').fetchone()[0]
    return {'sent': sent, 'failed': failed, 'queued': queued}
