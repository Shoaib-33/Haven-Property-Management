"""Documents shared with owners (insurance, deeds, valuations) and documents owners upload."""
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from . import db
from .db import audit, connect, now, transaction
from .main import choice, integer, read_upload, save_document
from .owners import owner_accounts
from .portal_access import PRIVATE_KINDS, notify, notify_staff
from .security import authorize, current_user, linked, record

router = APIRouter()
STAFF = ('manager', 'property', 'finance')
DOC_CATEGORIES = ('Insurance', 'Deed / title', 'Valuation', 'Energy label', 'Inspection report', 'Tax & levies', 'Other')


@router.post('/api/owner-documents')
def share_with_owner(data: dict, user=Depends(current_user)):
    company_id = integer(data, 'company_id')
    with transaction() as conn:
        authorize(conn, user, company_id, STAFF)
        owner = linked(conn, 'owners', integer(data, 'owner_id'), company_id)
        doc = linked(conn, 'documents', integer(data, 'document_id'), company_id)
        if doc['kind'].strip().casefold() in PRIVATE_KINDS:
            raise HTTPException(422, 'This document category is private and cannot be shared with owners.')
        title = (str(data.get('title', '')).strip() or doc['name'])[:150]
        category = choice(data, 'category', DOC_CATEGORIES)
        conn.execute('INSERT INTO owner_documents(company_id,owner_id,document_id,title,category,shared_by,shared_at) VALUES(?,?,?,?,?,?,?) ON CONFLICT(owner_id,document_id) DO UPDATE SET revoked=0,title=excluded.title,category=excluded.category,shared_by=excluded.shared_by,shared_at=excluded.shared_at,from_owner=0', (company_id, owner['id'], doc['id'], title, category, user['id'], now()))
        ids = [r[0] for r in conn.execute('SELECT user_id FROM owner_accounts WHERE owner_id=? AND enabled=1', (owner['id'],))]
        notify(conn, company_id, ids, 'New document available', title, 'documents')
        audit(conn, user['id'], company_id, 'shared', 'owner_document', doc['id'], {'owner_id': owner['id'], 'category': category})
    return {'ok': True}


@router.post('/api/owner-documents/{share_id}/revoke')
def revoke_owner_document(share_id: int, user=Depends(current_user)):
    with transaction() as conn:
        s = record(conn, 'owner_documents', share_id, user, STAFF)
        conn.execute('UPDATE owner_documents SET revoked=1 WHERE id=?', (share_id,))
        audit(conn, user['id'], s['company_id'], 'sharing_revoked', 'owner_document', s['document_id'], {'owner_id': s['owner_id']})
    return {'ok': True}


@router.get('/api/owner/documents/{share_id}/file')
def owner_document_file(share_id: int, user=Depends(current_user)):
    with transaction() as conn:
        accounts = owner_accounts(conn, user)
        marks = ','.join('?' for _ in accounts)
        doc = conn.execute(f'SELECT d.* FROM owner_documents s JOIN documents d ON d.id=s.document_id WHERE s.id=? AND s.revoked=0 AND s.owner_id IN ({marks})', (share_id, *[a['id'] for a in accounts])).fetchone()
        if not doc:
            raise HTTPException(404, 'Document not found.')
        doc = dict(doc)
        audit(conn, user['id'], doc['company_id'], 'downloaded', 'document', doc['id'], {})
    return FileResponse(db.DATA_DIR / 'uploads' / doc['file_key'], media_type=doc['mime'], filename=doc['name'])


@router.post('/api/owner/documents')
async def owner_upload(category: str = Form(...), title: str = Form(''), file: UploadFile = File(...), user=Depends(current_user)):
    with connect() as conn:
        accounts = owner_accounts(conn, user)
    if len(accounts) != 1:
        raise HTTPException(409, 'Contact your property manager to upload for the correct company.')
    if category not in DOC_CATEGORIES:
        raise HTTPException(422, 'Choose a document category.')
    upload = await read_upload(file)
    account = accounts[0]
    label = (title.strip() or upload[3])[:150]
    with transaction() as conn:
        doc_id = save_document(conn, account['company_id'], None, user['id'], 'Owner upload', upload)
        conn.execute('INSERT INTO owner_documents(company_id,owner_id,document_id,title,category,shared_by,from_owner,shared_at) VALUES(?,?,?,?,?,?,1,?)', (account['company_id'], account['id'], doc_id, label, category, user['id'], now()))
        notify_staff(conn, account['company_id'], 'Owner uploaded a document', f'{account["name"]}: {label[:100]}', 'documents')
        audit(conn, user['id'], account['company_id'], 'uploaded', 'owner_document', doc_id, {'category': category})
    return {'ok': True}
