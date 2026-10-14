"""Per-urgency repair deadlines: how fast staff must first respond and when work should be resolved."""
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException

from .db import audit, now, transaction
from .security import authorize, current_user

router = APIRouter()
# (first response within hours, resolved within days). Routine keeps the 6-week written-notice window.
DEFAULTS = {'Emergency': (4, 2), 'Urgent': (48, 14), 'Routine': (120, 42)}


def policy(conn, company_id, urgency):
    row = conn.execute('SELECT response_hours,resolve_days FROM sla_policies WHERE company_id=? AND urgency=?', (company_id, urgency)).fetchone()
    return (row['response_hours'], row['resolve_days']) if row else DEFAULTS.get(urgency, DEFAULTS['Routine'])


def stamp(conn, company_id, urgency, reported_on):
    hours, days = policy(conn, company_id, urgency)
    response_due = (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat(timespec='seconds')
    return {'response_due_at': response_due, 'due_date': (date.fromisoformat(reported_on) + timedelta(days=days)).isoformat()}


def mark_responded(conn, repair_id):
    conn.execute('UPDATE repairs SET responded_at=? WHERE id=? AND responded_at IS NULL', (now(), repair_id))


def policies_for(conn, company_id):
    return [{'urgency': u, 'response_hours': policy(conn, company_id, u)[0], 'resolve_days': policy(conn, company_id, u)[1]} for u in DEFAULTS]


@router.post('/api/sla-policies')
def save_policies(data: dict, user=Depends(current_user)):
    try:
        company_id = int(data['company_id'])
        items = data['policies']
        parsed = {}
        for urgency in DEFAULTS:
            hours, days = int(items[urgency]['response_hours']), int(items[urgency]['resolve_days'])
            if not (1 <= hours <= 720 and 1 <= days <= 90):
                raise ValueError()
            parsed[urgency] = (hours, days)
    except (KeyError, TypeError, ValueError):
        raise HTTPException(422, 'Enter response hours (1-720) and resolution days (1-90) for each urgency.')
    if not (parsed['Emergency'][0] <= parsed['Urgent'][0] <= parsed['Routine'][0]):
        raise HTTPException(422, 'Emergency must be answered at least as fast as Urgent, and Urgent at least as fast as Routine.')
    with transaction() as conn:
        authorize(conn, user, company_id, ('manager',))
        for urgency, (hours, days) in parsed.items():
            conn.execute('INSERT INTO sla_policies(company_id,urgency,response_hours,resolve_days) VALUES(?,?,?,?) ON CONFLICT(company_id,urgency) DO UPDATE SET response_hours=excluded.response_hours,resolve_days=excluded.resolve_days', (company_id, urgency, hours, days))
        audit(conn, user['id'], company_id, 'sla_updated', 'company', company_id, {u: {'response_hours': h, 'resolve_days': d} for u, (h, d) in parsed.items()})
    return {'ok': True}
