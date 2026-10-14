import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, Request

from .db import connect


PBKDF2_ROUNDS = 600000
LEGACY_ROUNDS = 310000


def password_hash(password):
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), PBKDF2_ROUNDS).hex()
    return f'pbkdf2:{PBKDF2_ROUNDS}:{salt}:{digest}'


def _parse_hash(encoded):
    parts = encoded.split(':')
    if len(parts) == 4 and parts[0] == 'pbkdf2':
        return int(parts[1]), parts[2], parts[3]
    salt, digest = parts
    return LEGACY_ROUNDS, salt, digest


def verify_password(password, encoded):
    rounds, salt, digest = _parse_hash(encoded)
    actual = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), rounds).hex()
    return hmac.compare_digest(actual, digest)


def needs_rehash(encoded):
    return _parse_hash(encoded)[0] < PBKDF2_ROUNDS


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def expiry(days=1):
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat(timespec='seconds')


PORTALS = ('tenant', 'contractor', 'owner')


def portal_of(request: Request):
    portal = request.headers.get('X-Haven-Portal', '')
    if portal not in PORTALS:
        portal = next((p for p in PORTALS if request.url.path.startswith('/api/' + p + '/')), '')
    return portal


def session_cookie(request: Request):
    portal = portal_of(request)
    return 'haven_' + portal + '_session' if portal else 'haven_session'


def current_user(request: Request):
    cookie = session_cookie(request)
    token = request.cookies.get(cookie, '')
    with connect() as conn:
        row = conn.execute('SELECT u.*,s.csrf,s.expires_at FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=? AND u.active=1', (token_hash(token),)).fetchone()
    if not row or row['expires_at'] <= datetime.now(timezone.utc).isoformat(timespec='seconds'):
        raise HTTPException(401, 'Please sign in to continue.')
    if request.method not in ('GET', 'HEAD', 'OPTIONS') and not hmac.compare_digest(request.headers.get('X-CSRF-Token', ''), row['csrf']):
        raise HTTPException(403, 'Session verification failed. Refresh and try again.')
    return dict(row)


def authorize(conn, user, company_id, roles=None):
    row = conn.execute('SELECT role FROM memberships WHERE user_id=? AND company_id=?', (user['id'], company_id)).fetchone()
    if not row:
        raise HTTPException(403, 'You do not have access to this company.')
    role = row['role']
    if roles and role not in roles and role != 'admin':
        raise HTTPException(403, 'Your role cannot perform this action.')
    return role


def authorize_property(conn,user,company_id,property_id):
    role=authorize(conn,user,company_id)
    if role=='inspector' and not conn.execute('SELECT 1 FROM inspector_assignments WHERE user_id=? AND company_id=? AND property_id=?',(user['id'],company_id,property_id)).fetchone():
        raise HTTPException(403,'This property is not assigned to you.')
    return role


def record(conn, table, record_id, user, roles=None):
    # table is always a literal supplied by our own endpoint, never request input.
    row = conn.execute(f'SELECT * FROM {table} WHERE id=?', (record_id,)).fetchone()
    if not row:
        raise HTTPException(404, 'Record not found.')
    row=dict(row)
    role=authorize(conn, user, row['company_id'], roles)
    if role=='inspector':
        property_id=row.get('property_id')
        contract_id=row.get('contract_id')
        if not property_id and contract_id:
            parent=conn.execute('SELECT property_id FROM contracts WHERE id=? AND company_id=?',(contract_id,row['company_id'])).fetchone()
            property_id=parent['property_id'] if parent else None
        if not property_id:
            raise HTTPException(403,'Inspectors can access only their assigned properties.')
        authorize_property(conn,user,row['company_id'],property_id)
    return row


def linked(conn, table, record_id, company_id):
    row = conn.execute(f'SELECT * FROM {table} WHERE id=? AND company_id=?', (record_id, company_id)).fetchone()
    if not row:
        raise HTTPException(400, 'The linked record does not belong to this company.')
    return dict(row)
