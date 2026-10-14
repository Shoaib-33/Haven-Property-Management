"""Helpdesk-style extras for tenant conversations: thread status, assignment and saved replies."""
from fastapi import APIRouter, Depends, HTTPException

from .db import audit, now, transaction
from .main import choice, insert, integer, text_field
from .portal_access import notify_tenant
from .security import authorize, current_user, record

router = APIRouter()
STAFF = ('manager', 'property', 'finance')

STARTER_REPLIES = [
    ('Received, we are on it', 'Hi {tenant}, thank you for your message. We have received it and will come back to you shortly.'),
    ('Need more information', 'Hi {tenant}, to help you quickly we need a little more information. Could you describe what happened and, if possible, add a photo?'),
    ('Repair scheduled', 'Hi {tenant}, we have arranged for someone to look at this at {property}. You will receive the appointment details in the portal.'),
    ('Payment received', 'Hi {tenant}, we have received and checked your payment. Thank you, your balance has been updated.'),
    ('Resolved, anything else?', 'Hi {tenant}, we have marked this as resolved. If anything is still wrong, just reply here and we will reopen it.'),
]


@router.post('/api/threads/{thread_id}/state')
def set_state(thread_id: int, data: dict, user=Depends(current_user)):
    status = choice(data, 'status', ('open', 'resolved'))
    with transaction() as conn:
        root = record(conn, 'portal_messages', thread_id, user, STAFF)
        if root['thread_id'] not in (None, thread_id):
            raise HTTPException(422, 'Choose the first message of a conversation.')
        assignee = None
        if data.get('assignee_id'):
            assignee = integer(data, 'assignee_id')
            if not conn.execute('SELECT 1 FROM memberships WHERE user_id=? AND company_id=? AND role IN ("admin","manager","property","finance")', (assignee, root['company_id'])).fetchone():
                raise HTTPException(422, 'Choose a staff member of this company.')
        conn.execute('INSERT INTO thread_states(thread_id,company_id,status,assignee_id,updated_by,updated_at) VALUES(?,?,?,?,?,?) ON CONFLICT(thread_id) DO UPDATE SET status=excluded.status,assignee_id=excluded.assignee_id,updated_by=excluded.updated_by,updated_at=excluded.updated_at', (thread_id, root['company_id'], status, assignee, user['id'], now()))
        if status == 'resolved':
            notify_tenant(conn, root['contract_id'], 'Conversation resolved', root['subject'], 'messages')
        audit(conn, user['id'], root['company_id'], 'thread_' + status, 'portal_message', thread_id, {'assignee_id': assignee})
    return {'ok': True}


@router.post('/api/canned-replies')
def add_reply(data: dict, user=Depends(current_user)):
    company_id = integer(data, 'company_id')
    with transaction() as conn:
        authorize(conn, user, company_id, STAFF)
        rid = insert(conn, 'canned_replies', {'company_id': company_id, 'title': text_field(data, 'title', maximum=80), 'body': text_field(data, 'body', maximum=1500), 'created_by': user['id'], 'created_at': now()})
        audit(conn, user['id'], company_id, 'created', 'canned_reply', rid, {})
    return {'id': rid}


@router.delete('/api/canned-replies/{reply_id}')
def delete_reply(reply_id: int, user=Depends(current_user)):
    with transaction() as conn:
        r = record(conn, 'canned_replies', reply_id, user, STAFF)
        conn.execute('DELETE FROM canned_replies WHERE id=?', (reply_id,))
        audit(conn, user['id'], r['company_id'], 'deleted', 'canned_reply', reply_id, {})
    return {'ok': True}


@router.post('/api/canned-replies/starters')
def starters(data: dict, user=Depends(current_user)):
    company_id = integer(data, 'company_id')
    added = 0
    with transaction() as conn:
        authorize(conn, user, company_id, STAFF)
        for title, body in STARTER_REPLIES:
            if not conn.execute('SELECT 1 FROM canned_replies WHERE company_id=? AND title=?', (company_id, title)).fetchone():
                insert(conn, 'canned_replies', {'company_id': company_id, 'title': title, 'body': body, 'created_by': user['id'], 'created_at': now()})
                added += 1
    return {'added': added}
