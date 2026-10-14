import json
import secrets
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response

from .clock import local_today
from . import db, sla
from .db import audit, connect, now, transaction
from .security import authorize, current_user, expiry, linked, password_hash, record, token_hash, verify_password
from .portal_access import contractor_job, notify, notify_staff, notify_tenant, share_document, tenant_contract, tenant_record
from .main import date_field, euros, insert, integer, invoice_balance, invoice_pdf_bytes, money, pdf_response, read_upload, save_document, submit_proof, task, text_field, choice

router = APIRouter()


@router.post('/api/account/password')
def change_password(data: dict, user=Depends(current_user)):
    old = text_field(data, 'current_password', maximum=1024)
    new = text_field(data, 'new_password', maximum=128)
    if len(new) < 12:
        raise HTTPException(422, 'Use at least 12 characters for your new password.')
    with transaction() as conn:
        current = conn.execute('SELECT password_hash FROM users WHERE id=?', (user['id'],)).fetchone()
        if not verify_password(old, current['password_hash']):
            raise HTTPException(403, 'Current password is incorrect.')
        conn.execute('UPDATE users SET password_hash=? WHERE id=?', (password_hash(new), user['id']))
        conn.execute('DELETE FROM sessions WHERE user_id=?', (user['id'],))
        audit(conn, user['id'], None, 'password_changed', 'user', user['id'], {})
    return {'ok': True, 'sign_in_required': True}


@router.post('/api/accounts/invite')
def invite_account(data: dict, user=Depends(current_user)):
    kind = choice(data, 'kind', ('tenant','contractor','owner'))
    table = {'tenant': 'tenants', 'contractor': 'contractors', 'owner': 'owners'}[kind]
    with transaction() as conn:
        target = record(conn, table, integer(data,'target_id'), user, ('manager','property'))
        email = target['email'].strip().lower()
        if conn.execute('SELECT id FROM users WHERE email=?', (email,)).fetchone():
            raise HTTPException(409, 'An account with this email already exists. Use the existing account access controls; invitations cannot replace existing credentials.')
        account_table, target_key = {'tenant': ('portal_accounts', 'tenant_id'), 'contractor': ('contractor_accounts', 'contractor_id'), 'owner': ('owner_accounts', 'owner_id')}[kind]
        if conn.execute(f'SELECT 1 FROM {account_table} WHERE {target_key}=?', (target['id'],)).fetchone():
            raise HTTPException(409, 'This record already has a portal account. Enable its access instead.')
        conn.execute('UPDATE account_invitations SET revoked=1 WHERE company_id=? AND kind=? AND target_id=? AND used_at IS NULL', (target['company_id'],kind,target['id']))
        token = secrets.token_urlsafe(32)
        iid = insert(conn,'account_invitations',{'company_id':target['company_id'],'email':email,'name':target['name'],'kind':kind,'target_id':target['id'],'token_hash':token_hash(token),'expires_at':expiry(7),'created_by':user['id'],'created_at':now()})
        audit(conn,user['id'],target['company_id'],'invited',kind,target['id'],{'invitation_id':iid})
    return {'path':'/activate/'+token,'email':email,'expires_in_days':7}


def invitation(conn, token):
    row = conn.execute('SELECT * FROM account_invitations WHERE token_hash=? AND revoked=0 AND used_at IS NULL AND expires_at>?',(token_hash(token),now())).fetchone()
    if not row:
        raise HTTPException(404,'This invitation has expired or was already used. Ask your property manager for help.')
    return dict(row)


@router.get('/api/invitations/{token}')
def invitation_details(token: str):
    with connect() as conn:
        i=invitation(conn,token)
        return {'name':i['name'],'email':i['email'],'kind':i['kind'],'company':conn.execute('SELECT name FROM companies WHERE id=?',(i['company_id'],)).fetchone()[0]}


@router.post('/api/invitations/{token}')
def accept_invitation(token: str,data: dict):
    password=text_field(data,'password',maximum=128)
    if len(password)<12:
        raise HTTPException(422,'Use a password of at least 12 characters.')
    with transaction() as conn:
        i=invitation(conn,token)
        if conn.execute('SELECT 1 FROM users WHERE email=?',(i['email'],)).fetchone():
            raise HTTPException(409,'An account already uses this email. Contact your property manager.')
        table, key = {'tenant': ('portal_accounts','tenant_id'), 'contractor': ('contractor_accounts','contractor_id'), 'owner': ('owner_accounts','owner_id')}[i['kind']]
        if conn.execute(f'SELECT 1 FROM {table} WHERE {key}=?',(i['target_id'],)).fetchone():
            raise HTTPException(409,'This record is already linked to an account.')
        uid=insert(conn,'users',{'name':i['name'],'email':i['email'],'password_hash':password_hash(password)})
        insert(conn,table,{'user_id':uid,key:i['target_id'],'company_id':i['company_id'],'created_at':now()})
        conn.execute('UPDATE account_invitations SET used_at=? WHERE id=?',(now(),i['id']))
        audit(conn,uid,i['company_id'],'activated',i['kind'],i['target_id'],{})
        notify(conn,i['company_id'],[uid],'Welcome to Haven','Your account is ready.','home')
    return {'ok':True,'email':i['email']}


@router.post('/api/accounts/access')
def account_access(data: dict,user=Depends(current_user)):
    kind=choice(data,'kind',('tenant','contractor','owner'))
    table,key,target_table={'tenant':('portal_accounts','tenant_id','tenants'),'contractor':('contractor_accounts','contractor_id','contractors'),'owner':('owner_accounts','owner_id','owners')}[kind]
    if not isinstance(data.get('enabled'),bool):
        raise HTTPException(422,'Choose whether access is enabled.')
    with transaction() as conn:
        target=record(conn,target_table,integer(data,'target_id'),user,('manager',))
        row=conn.execute(f'SELECT * FROM {table} WHERE {key}=?',(target['id'],)).fetchone()
        if not row:
            raise HTTPException(404,'This record has no portal account.')
        conn.execute(f'UPDATE {table} SET enabled=? WHERE {key}=?',(int(data['enabled']),target['id']))
        audit(conn,user['id'],target['company_id'],'access_changed',kind,target['id'],{'enabled':data['enabled']})
    return {'ok':True}


@router.get('/api/notifications')
def notifications(user=Depends(current_user)):
    with connect() as conn:
        # A revoked account must not continue reading notices associated with that company.
        result=conn.execute('SELECT n.* FROM notifications n WHERE n.user_id=? AND (EXISTS(SELECT 1 FROM memberships m WHERE m.user_id=n.user_id AND m.company_id=n.company_id) OR EXISTS(SELECT 1 FROM portal_accounts a WHERE a.user_id=n.user_id AND a.company_id=n.company_id AND a.enabled=1) OR EXISTS(SELECT 1 FROM contractor_accounts a WHERE a.user_id=n.user_id AND a.company_id=n.company_id AND a.enabled=1) OR EXISTS(SELECT 1 FROM owner_accounts a WHERE a.user_id=n.user_id AND a.company_id=n.company_id AND a.enabled=1)) ORDER BY n.id DESC LIMIT 100',(user['id'],)).fetchall()
        return [dict(r) for r in result]


@router.post('/api/notifications/{notification_id}/read')
def read_notification(notification_id:int,user=Depends(current_user)):
    with transaction() as conn:
        conn.execute('UPDATE notifications SET is_read=1 WHERE id=? AND user_id=?',(notification_id,user['id']))
    return {'ok':True}


@router.get('/api/tenant/data')
def tenant_data(user=Depends(current_user)):
    with connect() as conn:
        accounts=[dict(r) for r in conn.execute('SELECT t.*,a.user_id FROM portal_accounts a JOIN tenants t ON t.id=a.tenant_id WHERE a.user_id=? AND a.enabled=1',(user['id'],))]
        if not accounts:
            raise HTTPException(403,'Tenant access is not enabled for this account.')
        contracts=[dict(r) for r in conn.execute('SELECT c.*,p.name property_name,p.address,p.city,p.postcode,p.area,p.rooms,p.energy_label,co.name company_name,co.payment_instructions FROM contracts c JOIN portal_accounts a ON a.tenant_id=c.tenant_id AND a.company_id=c.company_id JOIN properties p ON p.id=c.property_id JOIN companies co ON co.id=c.company_id WHERE a.user_id=? AND a.enabled=1 ORDER BY c.start_date DESC',(user['id'],))]
        ids=[c['id'] for c in contracts]
        company_ids=sorted({a['company_id'] for a in accounts})
        result={'accounts':accounts,'contracts':contracts,'today':local_today().isoformat()}
        from .lease_offers import tenant_offers
        result['lease_offers']=tenant_offers(conn,user['id'])
        from .schedule import payment_schedule
        result['payment_schedules']=[payment_schedule(conn,c) for c in contracts if c['status'] in ('active','notice given')]
        if company_ids:
            marks=','.join('?' for _ in company_ids)
            result['notifications']=[dict(r) for r in conn.execute(f'SELECT id,title,body,target,is_read,created_at FROM notifications WHERE user_id=? AND company_id IN ({marks}) ORDER BY id DESC LIMIT 30',(user['id'],*company_ids))]
        else:
            result['notifications']=[]
        if not ids:
            for name in ('invoices','ledger','payment_proofs','documents','repairs','repair_updates','messages','requests','deposits','rent_versions','inspections','payment_plans','instalments','appointments','thread_states'):
                result[name]=[]
            return result
        placeholders=','.join('?' for _ in ids)
        for table,key,columns in [
            ('invoices','invoices','*'),('rent_versions','rent_versions','id,contract_id,effective_from,basic_cents,service_cents'),
            ('ledger','ledger','id,contract_id,invoice_id,kind,amount_cents,created_at'),
            ('deposit_events','deposits','id,contract_id,kind,amount_cents,status,reason,created_at'),
            ('repairs','repairs','id,contract_id,title,category,urgency,description,status,reported_on,due_date,written,completed_at'),
            ('requests','requests','id,contract_id,kind,subject,body,requested_date,status,response,created_at,responded_at'),
            ('inspections','inspections','id,contract_id,kind,inspected_on,findings,status,document_id'),
            ('payment_plans','payment_plans','id,contract_id,title,total_cents,instalment_cents,first_due,interval_days,status')]:
            clause=' AND status="final"' if table=='inspections' else ' AND status<>"pending"' if table=='payment_plans' else ' AND released_at IS NOT NULL' if table=='invoices' else ' AND (invoice_id IS NULL OR invoice_id IN (SELECT id FROM invoices WHERE released_at IS NOT NULL))' if table=='ledger' else ''
            result[key]=[dict(r) for r in conn.execute(f'SELECT {columns} FROM {table} WHERE contract_id IN ({placeholders}){clause} ORDER BY id DESC',ids)]
        result['invoices']=[{**r,'balance_cents':invoice_balance(conn,r['id'])} for r in result['invoices']]
        result['payment_proofs']=[dict(r) for r in conn.execute(f'SELECT p.id,p.invoice_id,p.document_id,p.amount_cents,p.paid_on,p.method,p.reference,p.note,p.status,p.review_note,p.created_at FROM payment_proofs p JOIN invoices i ON i.id=p.invoice_id WHERE i.contract_id IN ({placeholders}) ORDER BY p.id DESC',ids)]
        result['documents']=[dict(r) for r in conn.execute(f'SELECT d.id,d.name,d.kind,d.size,d.mime,s.contract_id,s.shared_at FROM shared_documents s JOIN documents d ON d.id=s.document_id WHERE s.contract_id IN ({placeholders}) AND s.revoked=0 ORDER BY s.id DESC',ids)]
        result['messages']=[dict(r) for r in conn.execute(f'SELECT p.id,p.contract_id,p.sender_id,p.subject,p.body,p.document_id,p.created_at,COALESCE(p.thread_id,p.id) thread_id,u.name sender_name FROM portal_messages p JOIN users u ON u.id=p.sender_id WHERE p.contract_id IN ({placeholders}) ORDER BY p.id DESC',ids)]
        result['thread_states']=[dict(r) for r in conn.execute(f'SELECT t.thread_id,t.status FROM thread_states t JOIN portal_messages m ON m.id=t.thread_id WHERE m.contract_id IN ({placeholders})',ids)]
        result['repair_updates']=[dict(r) for r in conn.execute(f'SELECT u.id,u.repair_id,u.message,u.document_id,u.created_at FROM repair_updates u JOIN repairs r ON r.id=u.repair_id WHERE r.contract_id IN ({placeholders}) AND u.tenant_visible=1 ORDER BY u.id DESC',ids)]
        result['appointments']=[dict(r) for r in conn.execute(f'SELECT w.id,w.repair_id,w.scheduled_for,w.status,c.name contractor_name,c.trade contractor_trade,c.phone contractor_phone,c.email contractor_email FROM work_orders w JOIN repairs r ON r.id=w.repair_id JOIN contractors c ON c.id=w.contractor_id WHERE r.contract_id IN ({placeholders}) ORDER BY w.id DESC',ids)]
        result['instalments']=[dict(r) for r in conn.execute(f'SELECT i.id,i.plan_id,i.due_date,i.amount_cents,i.status FROM plan_instalments i JOIN payment_plans p ON p.id=i.plan_id WHERE p.contract_id IN ({placeholders}) AND p.status="approved" ORDER BY i.due_date',ids)]
    for company_id in {c['company_id'] for c in contracts}:
        db.audit_view(user['id'],company_id,'tenant_portal',None)
    return result


@router.get('/api/tenant/invoices/{invoice_id}/pdf')
def tenant_invoice_pdf(invoice_id:int,download:int=0,user=Depends(current_user)):
    with transaction() as conn:
        inv=tenant_record(conn,user,'invoices',invoice_id)
        if not inv.get('released_at'):
            raise HTTPException(404,'Record not found.')
        snap=json.loads(inv['snapshot'])
        audit(conn,user['id'],inv['company_id'],'downloaded','invoice',invoice_id,{})
    return Response(invoice_pdf_bytes(inv,snap),media_type='application/pdf',headers={'Content-Disposition':f'{"attachment" if download else "inline"}; filename="{inv["number"]}.pdf"'})


@router.post('/api/tenant/invoices/{invoice_id}/proof')
async def tenant_payment(invoice_id:int,amount:str=Form(...),paid_on:str=Form(...),method:str=Form(...),reference:str=Form(''),note:str=Form(''),file:UploadFile=File(...),user=Depends(current_user)):
    with connect() as conn:
        if not tenant_record(conn,user,'invoices',invoice_id).get('released_at'):
            raise HTTPException(404,'Record not found.')
    upload=await read_upload(file)
    with transaction() as conn:
        inv=tenant_record(conn,user,'invoices',invoice_id)
        result=submit_proof(conn,inv,{'amount':amount,'paid_on':paid_on,'method':method,'reference':reference,'note':note},upload,user['id'])
        notify_staff(conn,inv['company_id'],'New payment receipt',f'{user["name"]} submitted proof for {inv["number"]}.','payments')
        return result


@router.get('/api/tenant/documents/{document_id}/file')
def tenant_document(document_id:int,download:int=0,user=Depends(current_user)):
    with transaction() as conn:
        doc=conn.execute('SELECT * FROM documents WHERE id=?',(document_id,)).fetchone()
        if not doc:
            raise HTTPException(404,'Document not found.')
        # Share scope is explicit; merely being linked to a tenancy never publishes an internal document.
        shared=conn.execute('SELECT s.contract_id FROM shared_documents s JOIN contracts c ON c.id=s.contract_id JOIN portal_accounts a ON a.tenant_id=c.tenant_id AND a.company_id=c.company_id WHERE s.document_id=? AND s.revoked=0 AND a.user_id=? AND a.enabled=1',(document_id,user['id'])).fetchone()
        own_proof=conn.execute('SELECT i.contract_id FROM payment_proofs p JOIN invoices i ON i.id=p.invoice_id JOIN contracts c ON c.id=i.contract_id JOIN portal_accounts a ON a.tenant_id=c.tenant_id AND a.company_id=c.company_id WHERE p.document_id=? AND a.user_id=? AND a.enabled=1',(document_id,user['id'])).fetchone()
        if not shared and not own_proof:
            raise HTTPException(404,'Document not found.')
        audit(conn,user['id'],doc['company_id'],'downloaded' if download else 'viewed','document',document_id,{})
        filename=doc['file_key']
        mime,name=doc['mime'],doc['name']
    return FileResponse(db.DATA_DIR/'uploads'/filename,media_type=mime,filename=name,content_disposition_type='attachment' if download else 'inline')


@router.post('/api/tenant/repairs')
async def tenant_repair(contract_id:int=Form(...),title:str=Form(...),description:str=Form(...),category:str=Form('General'),urgency:str=Form('Routine'),file:UploadFile|None=File(None),user=Depends(current_user)):
    data={'title':title,'description':description,'category':category,'urgency':urgency}
    title=text_field(data,'title',maximum=180)
    description=text_field(data,'description')
    category=choice(data,'category',('Plumbing','Heating','Electrical','Damp / mould','Appliance','General'))
    urgency=choice(data,'urgency',('Routine','Urgent','Emergency'))
    with connect() as conn:
        c=tenant_contract(conn,user,contract_id)
        if c['end_date'] and c['end_date']<local_today().isoformat():
            raise HTTPException(409,'This tenancy has ended. Use Messages to contact your manager.')
    upload=await read_upload(file) if file and file.filename else None
    with transaction() as conn:
        c=tenant_contract(conn,user,contract_id)
        stamped=sla.stamp(conn,c['company_id'],urgency,local_today().isoformat())
        rid=insert(conn,'repairs',{'company_id':c['company_id'],'contract_id':contract_id,'title':title,'description':description,'category':category,'urgency':urgency,'reported_on':local_today().isoformat(),'written':1,'due_date':stamped['due_date'],'response_due_at':stamped['response_due_at'],'created_at':now()})
        doc_id=save_document(conn,c['company_id'],contract_id,user['id'],'Repair evidence',upload) if upload else None
        if doc_id:
            share_document(conn,c['company_id'],contract_id,doc_id,user['id'])
        insert(conn,'repair_updates',{'company_id':c['company_id'],'repair_id':rid,'user_id':user['id'],'message':'Tenant reported: '+description,'document_id':doc_id,'tenant_visible':1,'created_at':now()})
        task(conn,c['company_id'],contract_id,f'Triage tenant repair #{rid}: {title}',local_today().isoformat(),'Emergency' if urgency=='Emergency' else 'Repair')
        task(conn,c['company_id'],contract_id,f'Review day-35 repair #{rid}',(local_today()+timedelta(days=35)).isoformat(),'Repair')
        notify_staff(conn,c['company_id'],'New tenant repair',title,'repairs')
        audit(conn,user['id'],c['company_id'],'reported','repair',rid,{'title':title,'urgency':urgency})
    return {'id':rid}


@router.post('/api/tenant/repairs/{repair_id}/follow-up')
def tenant_repair_followup(repair_id:int,data:dict,user=Depends(current_user)):
    message=text_field(data,'message')
    with transaction() as conn:
        r=tenant_record(conn,user,'repairs',repair_id)
        insert(conn,'repair_updates',{'company_id':r['company_id'],'repair_id':repair_id,'user_id':user['id'],'message':message,'tenant_visible':1,'created_at':now()})
        if data.get('reopen') is True and r['status'] in ('done','closed'):
            conn.execute('UPDATE repairs SET status="reopened",completed_at=NULL WHERE id=?',(repair_id,))
            task(conn,r['company_id'],r['contract_id'],f'Tenant reopened repair #{repair_id}',local_today().isoformat(),'Repair')
        notify_staff(conn,r['company_id'],'Repair follow-up',message[:150],'repairs')
        audit(conn,user['id'],r['company_id'],'follow_up','repair',repair_id,{'message':message})
    return {'ok':True}


@router.post('/api/tenant/messages')
async def tenant_message(contract_id:int=Form(...),subject:str=Form(...),body:str=Form(...),thread_id:int|None=Form(None),file:UploadFile|None=File(None),user=Depends(current_user)):
    subject=text_field({'subject':subject},'subject',maximum=200)
    body=text_field({'body':body},'body')
    with connect() as conn:
        tenant_contract(conn,user,contract_id)
    upload=await read_upload(file) if file and file.filename else None
    with transaction() as conn:
        c=tenant_contract(conn,user,contract_id)
        doc_id=save_document(conn,c['company_id'],contract_id,user['id'],'Tenant message attachment',upload) if upload else None
        if thread_id:
            thread=conn.execute('SELECT * FROM portal_messages WHERE id=? AND contract_id=? AND (thread_id IS NULL OR thread_id=id)',(thread_id,contract_id)).fetchone()
            if not thread:
                raise HTTPException(404,'Message conversation not found for this tenancy.')
            subject=thread['subject']
        if doc_id:
            share_document(conn,c['company_id'],contract_id,doc_id,user['id'])
        mid=insert(conn,'portal_messages',{'company_id':c['company_id'],'contract_id':contract_id,'sender_id':user['id'],'subject':subject,'body':body,'document_id':doc_id,'thread_id':thread_id,'created_at':now()})
        if thread_id:
            conn.execute("UPDATE thread_states SET status='open',updated_at=? WHERE thread_id=? AND status='resolved'",(now(),thread_id))
        notify_staff(conn,c['company_id'],'Message from '+user['name'] if not thread_id else 'Tenant replied: '+user['name'],subject,'portal-inbox' if subject.startswith('Response: ') else 'tenant-messages')  # a reply to an answer on a request belongs to that ticket
        audit(conn,user['id'],c['company_id'],'sent','portal_message',mid,{'subject':subject,'thread_id':thread_id})
    return {'id':mid}


@router.post('/api/tenant/threads/{thread_id}/state')
def tenant_thread_state(thread_id:int,data:dict,user=Depends(current_user)):
    status=choice(data,'status',('open','resolved'))
    with transaction() as conn:
        root=tenant_record(conn,user,'portal_messages',thread_id)
        if root['thread_id'] not in (None,thread_id):
            raise HTTPException(422,'Choose the first message of a conversation.')
        conn.execute('INSERT INTO thread_states(thread_id,company_id,status,updated_by,updated_at) VALUES(?,?,?,?,?) ON CONFLICT(thread_id) DO UPDATE SET status=excluded.status,updated_by=excluded.updated_by,updated_at=excluded.updated_at',(thread_id,root['company_id'],status,user['id'],now()))
        notify_staff(conn,root['company_id'],'Tenant marked a conversation '+('solved' if status=='resolved' else 'open again'),root['subject'],'portal-inbox' if root['subject'].startswith('Response: ') else 'tenant-messages')
        audit(conn,user['id'],root['company_id'],'tenant_thread_'+status,'portal_message',thread_id,{})
    return {'ok':True}


@router.post('/api/tenant/requests')
def tenant_request(data:dict,user=Depends(current_user)):
    kind=choice(data,'kind',('Complaint','Termination notice','Payment plan','General request'))
    subject=text_field(data,'subject',maximum=200)
    body=text_field(data,'body')
    requested=date_field(data,'requested_date') if data.get('requested_date') else None
    with transaction() as conn:
        c=tenant_contract(conn,user,integer(data,'contract_id'))
        if kind=='Termination notice' and (not requested or requested<local_today().isoformat()):
            raise HTTPException(422,'Choose your requested future move-out date.')
        rid=insert(conn,'requests',{'company_id':c['company_id'],'contract_id':c['id'],'kind':kind,'subject':subject,'body':body,'requested_date':requested,'created_by':user['id'],'created_at':now()})
        task(conn,c['company_id'],c['id'],f'Review {kind.lower()} #{rid}: {subject}',(local_today()+timedelta(days=1)).isoformat(),'Tenant request')
        notify_staff(conn,c['company_id'],kind+' received',subject,'portal-inbox')
        audit(conn,user['id'],c['company_id'],'submitted','request',rid,{'kind':kind})
    return {'id':rid,'status':'open'}


@router.post('/api/tenant/profile')
def tenant_profile(data:dict,user=Depends(current_user)):
    phone=text_field(data,'phone',maximum=50)
    language=choice(data,'language',('en','nl'))
    with transaction() as conn:
        account=conn.execute('SELECT * FROM portal_accounts WHERE user_id=? AND tenant_id=? AND enabled=1',(user['id'],integer(data,'tenant_id'))).fetchone()
        if not account:
            raise HTTPException(404,'Tenant profile not found.')
        old=conn.execute('SELECT phone,language FROM tenants WHERE id=?',(account['tenant_id'],)).fetchone()
        conn.execute('UPDATE tenants SET phone=?,language=? WHERE id=?',(phone,language,account['tenant_id']))
        audit(conn,user['id'],account['company_id'],'contact_updated','tenant',account['tenant_id'],{'old':dict(old),'phone':phone,'language':language})
    return {'ok':True}


@router.get('/api/tenant/requests/{request_id}/file')
def tenant_request_document(request_id:int,user=Depends(current_user)):
    with connect() as conn:
        req=tenant_record(conn,user,'requests',request_id)
        return {'request_id':req['id'],'status':req['status'],'response':req['response'],'requested_date':req['requested_date'],'responded_at':req['responded_at']}
