"""If-this-then-that rules for routine follow-up. Rules only create tasks and in-app notices;
they never send legal letters, emails or move money, and each rule fires once per record."""
import logging
import re
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException

from .db import audit, connect, now, transaction
from .main import choice, euros, insert, integer, task, text_field
from .portal_access import notify_staff, notify_tenant
from .security import authorize, current_user, record

router = APIRouter()
TRIGGERS = ('invoice_overdue', 'invoice_due_soon', 'contract_ending', 'repair_open', 'proof_pending')
ACTIONS = ('create_task', 'notify_staff', 'notify_tenant')
MAX_PER_RUN = 200


def render(template, values):
    """Replace {tenant}, {property}, {invoice}, {amount}, {days}, {date}; unknown names are left untouched."""
    return re.sub(r'\{(\w+)\}', lambda m: str(values.get(m.group(1), m.group(0))), template)


def _days(a, b):
    return (date.fromisoformat(a) - date.fromisoformat(b)).days


def candidates(conn, rule, today):
    cid, n = rule['company_id'], rule['days']
    found = []
    if rule['trigger'] in ('invoice_overdue', 'invoice_due_soon'):
        rows = conn.execute('SELECT i.id,i.number,i.due_date,i.contract_id,t.name tenant,p.name property FROM invoices i JOIN contracts c ON c.id=i.contract_id JOIN tenants t ON t.id=c.tenant_id JOIN properties p ON p.id=c.property_id WHERE i.company_id=? AND i.released_at IS NOT NULL ORDER BY i.due_date', (cid,)).fetchall()
        for r in rows:
            late = _days(today, r['due_date'])
            if rule['trigger'] == 'invoice_overdue' and late < max(n, 1):
                continue
            if rule['trigger'] == 'invoice_due_soon' and not (-n <= late <= 0):
                continue
            open_cents = conn.execute('SELECT COALESCE(SUM(amount_cents),0) FROM ledger WHERE invoice_id=?', (r['id'],)).fetchone()[0]
            if open_cents <= 0:
                continue
            found.append({'entity': 'invoice', 'id': r['id'], 'contract_id': r['contract_id'], 'vars': {'tenant': r['tenant'], 'property': r['property'], 'invoice': r['number'], 'amount': euros(open_cents), 'days': abs(late), 'date': r['due_date']}})
    elif rule['trigger'] == 'contract_ending':
        horizon = (date.fromisoformat(today) + timedelta(days=n)).isoformat()
        for r in conn.execute("SELECT c.id,c.end_date,t.name tenant,p.name property FROM contracts c JOIN tenants t ON t.id=c.tenant_id JOIN properties p ON p.id=c.property_id WHERE c.company_id=? AND c.status IN ('active','notice given') AND c.end_date IS NOT NULL AND c.end_date>=? AND c.end_date<=?", (cid, today, horizon)):
            found.append({'entity': 'contract', 'id': r['id'], 'contract_id': r['id'], 'vars': {'tenant': r['tenant'], 'property': r['property'], 'invoice': '', 'amount': '', 'days': _days(r['end_date'], today), 'date': r['end_date']}})
    elif rule['trigger'] == 'repair_open':
        for r in conn.execute("SELECT r.id,r.title,r.reported_on,r.contract_id,t.name tenant,p.name property FROM repairs r JOIN contracts c ON c.id=r.contract_id JOIN tenants t ON t.id=c.tenant_id JOIN properties p ON p.id=c.property_id WHERE r.company_id=? AND r.status NOT IN ('done','closed')", (cid,)):
            age = _days(today, r['reported_on'])
            if age >= n:
                found.append({'entity': 'repair', 'id': r['id'], 'contract_id': r['contract_id'], 'vars': {'tenant': r['tenant'], 'property': r['property'], 'invoice': r['title'], 'amount': '', 'days': age, 'date': r['reported_on']}})
    elif rule['trigger'] == 'proof_pending':
        for r in conn.execute("SELECT pp.id,pp.created_at,pp.amount_cents,i.number,i.contract_id,t.name tenant,p.name property FROM payment_proofs pp JOIN invoices i ON i.id=pp.invoice_id JOIN contracts c ON c.id=i.contract_id JOIN tenants t ON t.id=c.tenant_id JOIN properties p ON p.id=c.property_id WHERE pp.company_id=? AND pp.status='pending'", (cid,)):
            age = _days(today, r['created_at'][:10])
            if age >= n:
                found.append({'entity': 'proof', 'id': r['id'], 'contract_id': r['contract_id'], 'vars': {'tenant': r['tenant'], 'property': r['property'], 'invoice': r['number'], 'amount': euros(r['amount_cents']), 'days': age, 'date': r['created_at'][:10]}})
    return found


def run_company(conn, company_id, dry_run=False):
    today = date.today().isoformat()
    report = []
    for rule in [dict(r) for r in conn.execute('SELECT * FROM automation_rules WHERE company_id=? AND enabled=1 ORDER BY id', (company_id,))]:
        done = {(r['entity'], r['entity_id']) for r in conn.execute('SELECT entity,entity_id FROM automation_runs WHERE rule_id=?', (rule['id'],))}
        fresh = [c for c in candidates(conn, rule, today) if (c['entity'], c['id']) not in done][:MAX_PER_RUN]
        samples = []
        for c in fresh:
            title = render(rule['title'], c['vars'])[:200]
            body = render(rule['body'], c['vars'])[:1000]
            if not dry_run:
                if rule['action'] == 'create_task':
                    task(conn, company_id, c['contract_id'], title, today, 'Automation', rule['owner'])
                elif rule['action'] == 'notify_staff':
                    notify_staff(conn, company_id, title, body or title, 'tasks')
                else:
                    notify_tenant(conn, c['contract_id'], title, body or title, 'home')
                conn.execute('INSERT OR IGNORE INTO automation_runs(company_id,rule_id,entity,entity_id,summary,ran_at) VALUES(?,?,?,?,?,?)', (company_id, rule['id'], c['entity'], c['id'], title, now()))
            samples.append(title)
        report.append({'rule_id': rule['id'], 'name': rule['name'], 'matches': len(fresh), 'samples': samples[:5]})
    return report


def run_all_companies():
    """Scheduler entry point; a failure in one company never blocks the others."""
    with connect() as conn:
        companies = [r[0] for r in conn.execute('SELECT DISTINCT company_id FROM automation_rules WHERE enabled=1')]
    for company_id in companies:
        try:
            with transaction() as conn:
                run_company(conn, company_id)
        except Exception:
            logging.exception('Automation failed for company %s.', company_id)


def _validate(data):
    trigger = choice(data, 'trigger', TRIGGERS)
    action = choice(data, 'action', ACTIONS)
    days = integer(data, 'days')
    if not 0 <= days <= 365:
        raise HTTPException(422, 'Days must be between 0 and 365.')
    return {'name': text_field(data, 'name', maximum=120), 'trigger': trigger, 'days': days, 'action': action, 'title': text_field(data, 'title', maximum=200), 'body': str(data.get('body', '')).strip()[:1000], 'owner': (str(data.get('owner', '')).strip() or 'Property team')[:80]}


@router.post('/api/automation-rules')
def create_rule(data: dict, user=Depends(current_user)):
    company_id = integer(data, 'company_id')
    values = _validate(data)
    with transaction() as conn:
        authorize(conn, user, company_id, ('manager',))
        if conn.execute('SELECT COUNT(*) FROM automation_rules WHERE company_id=?', (company_id,)).fetchone()[0] >= 50:
            raise HTTPException(409, 'A company can have at most 50 automation rules.')
        rid = insert(conn, 'automation_rules', {'company_id': company_id, **values, 'created_by': user['id'], 'created_at': now()})
        audit(conn, user['id'], company_id, 'created', 'automation_rule', rid, {k: values[k] for k in ('name', 'trigger', 'days', 'action')})
    return {'id': rid}


@router.post('/api/automation-rules/{rule_id}/toggle')
def toggle_rule(rule_id: int, data: dict, user=Depends(current_user)):
    if not isinstance(data.get('enabled'), bool):
        raise HTTPException(422, 'Choose whether the rule is enabled.')
    with transaction() as conn:
        rule = record(conn, 'automation_rules', rule_id, user, ('manager',))
        conn.execute('UPDATE automation_rules SET enabled=? WHERE id=?', (int(data['enabled']), rule_id))
        audit(conn, user['id'], rule['company_id'], 'enabled' if data['enabled'] else 'disabled', 'automation_rule', rule_id, {})
    return {'ok': True}


@router.delete('/api/automation-rules/{rule_id}')
def delete_rule(rule_id: int, user=Depends(current_user)):
    with transaction() as conn:
        rule = record(conn, 'automation_rules', rule_id, user, ('manager',))
        conn.execute('DELETE FROM automation_rules WHERE id=?', (rule_id,))
        audit(conn, user['id'], rule['company_id'], 'deleted', 'automation_rule', rule_id, {'name': rule['name']})
    return {'ok': True}


@router.post('/api/automation-rules/run')
def run_now(data: dict, user=Depends(current_user)):
    company_id = integer(data, 'company_id')
    dry = data.get('dry_run') is True
    with transaction() as conn:
        authorize(conn, user, company_id, ('manager',))
        report = run_company(conn, company_id, dry_run=dry)
        if not dry:
            audit(conn, user['id'], company_id, 'automation_run', 'company', company_id, {'actions': sum(r['matches'] for r in report)})
    return {'dry_run': dry, 'report': report, 'total': sum(r['matches'] for r in report)}
