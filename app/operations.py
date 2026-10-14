import json
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from .clock import local_today
from . import db, sla
from .db import audit, connect, now, transaction
from .security import authorize, current_user, linked, record
from .portal_access import contractor_job, notify, notify_staff, notify_tenant, share_document
from .main import choice, date_field, euros, insert, integer, invoice_balance, money, pdf_response, read_upload, save_document, task, text_field

router=APIRouter()
OPS=('manager','property')
FIN=('manager','finance')


def staff_extras(conn,user,company_id,role):
    result={}
    if role=='inspector':
        result['contractors']=[]; result['work_orders']=[]; result['portal_messages']=[]; result['requests']=[]; result['repair_updates']=[]; result['shared_documents']=[]
        result['inspector_assignments']=[dict(r) for r in conn.execute('SELECT a.*,p.name property_name FROM inspector_assignments a JOIN properties p ON p.id=a.property_id WHERE a.user_id=? AND a.company_id=?',(user['id'],company_id))]
        result['notifications']=[dict(r) for r in conn.execute('SELECT * FROM notifications WHERE user_id=? AND company_id=? ORDER BY id DESC LIMIT 50',(user['id'],company_id))]
        return result
    for table in ('contractors','work_orders','portal_messages','requests','repair_updates','shared_documents'):
        result[table]=[dict(r) for r in conn.execute(f'SELECT * FROM {table} WHERE company_id=? ORDER BY rowid DESC',(company_id,))]
    for table,key in [('portal_accounts','tenant_id'),('contractor_accounts','contractor_id')]:
        result[table]=[dict(r) for r in conn.execute(f'SELECT a.{key},a.user_id,a.enabled,u.name,u.email FROM {table} a JOIN users u ON u.id=a.user_id WHERE a.company_id=?',(company_id,))]
    if role in ('admin','manager','property'):
        result['contractor_agreement_submissions']=[dict(r) for r in conn.execute('SELECT s.*,c.name contractor_name,d.name document_name FROM contractor_agreement_submissions s JOIN contractors c ON c.id=s.contractor_id JOIN documents d ON d.id=s.document_id WHERE s.company_id=? ORDER BY s.id DESC',(company_id,))]
    result['portal_messages']=[dict(r) for r in conn.execute('SELECT p.*,u.name sender_name FROM portal_messages p JOIN users u ON u.id=p.sender_id WHERE p.company_id=? ORDER BY p.id DESC',(company_id,))]
    if role in ('admin','manager','finance'):
        for table in ('credit_notes','payment_plans','plan_instalments','published_statements'):
            result[table]=[dict(r) for r in conn.execute(f'SELECT * FROM {table} WHERE company_id=? ORDER BY id DESC',(company_id,))]
    result['lease_offers']=[dict(r) for r in conn.execute('SELECT o.id,o.company_id,o.property_id,o.tenant_id,o.start_date,o.end_date,o.basic_cents,o.service_cents,o.deposit_cents,o.status,o.sent_at,o.signer_name,o.signed_at,o.response_note,o.contract_id,o.final_document_id,o.edited,o.tenant_signature_mode,(o.form_fields IS NOT NULL) has_form,(o.landlord_signature IS NOT NULL) landlord_signed,t.name tenant_name,p.name property_name FROM lease_offers o JOIN tenants t ON t.id=o.tenant_id JOIN properties p ON p.id=o.property_id WHERE o.company_id=? ORDER BY o.id DESC',(company_id,))]
    result['sla_policies']=sla.policies_for(conn,company_id)
    result['thread_states']=[dict(r) for r in conn.execute('SELECT * FROM thread_states WHERE company_id=?',(company_id,))]
    result['canned_replies']=[dict(r) for r in conn.execute('SELECT id,title,body FROM canned_replies WHERE company_id=? ORDER BY title',(company_id,))]
    result['owners']=[dict(r) for r in conn.execute('SELECT * FROM owners WHERE company_id=? ORDER BY name',(company_id,))]
    result['portfolios']=[dict(r) for r in conn.execute('SELECT * FROM portfolios WHERE company_id=? ORDER BY name',(company_id,))]
    result['owner_documents']=[dict(r) for r in conn.execute('SELECT * FROM owner_documents WHERE company_id=? ORDER BY id DESC',(company_id,))]
    result['doc_templates']=[dict(r) for r in conn.execute('SELECT * FROM doc_templates WHERE company_id=? ORDER BY archived,name',(company_id,))]
    result['custom_fields']=[dict(r) for r in conn.execute('SELECT * FROM custom_fields WHERE company_id=? ORDER BY entity,label',(company_id,))]
    result['custom_values']=[dict(r) for r in conn.execute('SELECT field_id,entity_id,value FROM custom_values WHERE company_id=?',(company_id,))]
    if role in ('admin','manager'):
        result['automation_rules']=[dict(r) for r in conn.execute('SELECT * FROM automation_rules WHERE company_id=? ORDER BY id',(company_id,))]
        result['automation_runs']=[dict(r) for r in conn.execute('SELECT * FROM automation_runs WHERE company_id=? ORDER BY id DESC LIMIT 40',(company_id,))]
    result['owner_accounts']=[dict(r) for r in conn.execute('SELECT a.owner_id,a.user_id,a.enabled,u.name,u.email FROM owner_accounts a JOIN users u ON u.id=a.user_id WHERE a.company_id=?',(company_id,))]
    if role in ('admin','manager','finance'):
        result['owner_expenses']=[dict(r) for r in conn.execute('SELECT * FROM owner_expenses WHERE company_id=? ORDER BY incurred_on DESC,id DESC',(company_id,))]
        result['owner_statements']=[dict(r) for r in conn.execute('SELECT id,company_id,portfolio_id,period,collected_cents,fee_cents,expenses_cents,net_cents,status,created_by,published_at,paid_on,payout_reference,created_at FROM owner_statements WHERE company_id=? ORDER BY period DESC,id DESC',(company_id,))]
    result['notifications']=[dict(r) for r in conn.execute('SELECT * FROM notifications WHERE user_id=? AND company_id=? ORDER BY id DESC LIMIT 50',(user['id'],company_id))]
    return result


@router.post('/api/inspector-assignments')
def set_inspector_assignment(data:dict,user=Depends(current_user)):
    company_id=integer(data,'company_id'); staff_id=integer(data,'user_id'); property_id=integer(data,'property_id')
    with transaction() as conn:
        authorize(conn,user,company_id,('admin','manager'))
        member=conn.execute('SELECT role FROM memberships WHERE user_id=? AND company_id=?',(staff_id,company_id)).fetchone()
        prop=linked(conn,'properties',property_id,company_id)
        if not member or member['role']!='inspector':
            raise HTTPException(422,'Choose a staff member with the inspector role.')
        if data.get('assigned') is False:
            conn.execute('DELETE FROM inspector_assignments WHERE user_id=? AND property_id=? AND company_id=?',(staff_id,property_id,company_id))
            action='assignment_removed'
        else:
            conn.execute('INSERT OR IGNORE INTO inspector_assignments(user_id,company_id,property_id,assigned_by,assigned_at) VALUES(?,?,?,?,?)',(staff_id,company_id,property_id,user['id'],now()))
            action='assignment_added'
        audit(conn,user['id'],company_id,action,'inspector_assignment',staff_id,{'property_id':property_id})
    return {'ok':True}


@router.patch('/api/companies/{company_id}')
def edit_company(company_id:int,data:dict,user=Depends(current_user)):
    with transaction() as conn:
        authorize(conn,user,company_id,('admin',))
        old=dict(conn.execute('SELECT * FROM companies WHERE id=?',(company_id,)).fetchone())
        values={'name':text_field(data,'name',maximum=120),'address':text_field(data,'address',maximum=500),'kvk':str(data.get('kvk',''))[:50],'payment_instructions':text_field(data,'payment_instructions',maximum=2000)}
        conn.execute('UPDATE companies SET name=?,address=?,kvk=?,payment_instructions=? WHERE id=?',(*values.values(),company_id))
        audit(conn,user['id'],company_id,'updated','company',company_id,{'old':old,'new':values})
    return {'ok':True}


@router.patch('/api/tenants/{tenant_id}')
def edit_tenant(tenant_id:int,data:dict,user=Depends(current_user)):
    with transaction() as conn:
        t=record(conn,'tenants',tenant_id,user,OPS)
        email=text_field(data,'email',maximum=254)
        if '@' not in email:
            raise HTTPException(422,'Enter a valid email address.')
        values={'name':text_field(data,'name',maximum=120),'email':email,'phone':str(data.get('phone',''))[:50],'language':choice(data,'language',('nl','en'))}
        conn.execute('UPDATE tenants SET name=?,email=?,phone=?,language=? WHERE id=?',(*values.values(),tenant_id))
        audit(conn,user['id'],t['company_id'],'contact_updated','tenant',tenant_id,{'old':{k:t[k] for k in values},'new':values})
    return {'ok':True,'note':'Portal login email remains unchanged.'}


@router.patch('/api/properties/{property_id}')
def edit_property(property_id:int,data:dict,user=Depends(current_user)):
    with transaction() as conn:
        p=record(conn,'properties',property_id,user,OPS)
        label=choice(data,'energy_label',('Unknown','A++++','A+++','A++','A+','A','B','C','D','E','F','G'))
        name=text_field(data,'name',maximum=200)
        conn.execute('UPDATE properties SET name=?,energy_label=? WHERE id=?',(name,label,property_id))
        audit(conn,user['id'],p['company_id'],'updated','property',property_id,{'old':{'name':p['name'],'energy_label':p['energy_label']},'new':{'name':name,'energy_label':label}})
        if label!=p['energy_label']:
            task(conn,p['company_id'],None,f'Obtain updated WWS report for property #{property_id}',local_today().isoformat(),'Valuation')
    return {'ok':True}


@router.post('/api/staff/{staff_id}/role')
def update_role(staff_id:int,data:dict,user=Depends(current_user)):
    company_id=integer(data,'company_id')
    role=choice(data,'role',('admin','manager','finance','property','remove'))
    with transaction() as conn:
        authorize(conn,user,company_id,('admin',))
        member=conn.execute('SELECT role FROM memberships WHERE user_id=? AND company_id=?',(staff_id,company_id)).fetchone()
        if not member:
            raise HTTPException(404,'Staff member not found.')
        if member['role']=='admin' and role!='admin':
            count=conn.execute('SELECT COUNT(*) FROM memberships WHERE company_id=? AND role="admin"',(company_id,)).fetchone()[0]
            if count<=1:
                raise HTTPException(409,'Keep at least one administrator for the company.')
        if role=='remove':
            conn.execute('DELETE FROM memberships WHERE user_id=? AND company_id=?',(staff_id,company_id))
        else:
            conn.execute('UPDATE memberships SET role=? WHERE user_id=? AND company_id=?',(role,staff_id,company_id))
        audit(conn,user['id'],company_id,'role_changed','user',staff_id,{'from':member['role'],'to':role})
    return {'ok':True}


@router.post('/api/portal/messages')
def staff_message(data:dict,user=Depends(current_user)):
    with transaction() as conn:
        c=record(conn,'contracts',integer(data,'contract_id'),user,('property','finance','manager'))
        thread_id=integer(data,'thread_id') if data.get('thread_id') else None
        thread=None
        if thread_id:
            thread=conn.execute('SELECT * FROM portal_messages WHERE id=? AND contract_id=? AND (thread_id IS NULL OR thread_id=id)',(thread_id,c['id'])).fetchone()
            if not thread:
                raise HTTPException(404,'Message conversation not found for this tenancy.')
        doc_id=integer(data,'document_id') if data.get('document_id') else None
        if doc_id:
            doc=linked(conn,'documents',doc_id,c['company_id'])
            if doc['contract_id'] and doc['contract_id']!=c['id']:
                raise HTTPException(422,'This attachment belongs to another tenancy.')
            share_document(conn,c['company_id'],c['id'],doc_id,user['id'])
        subject=thread['subject'] if thread else text_field(data,'subject',maximum=200)
        mid=insert(conn,'portal_messages',{'company_id':c['company_id'],'contract_id':c['id'],'sender_id':user['id'],'subject':subject,'body':text_field(data,'body'),'document_id':doc_id,'thread_id':thread_id,'created_at':now()})
        notify_tenant(conn,c['id'],'Reply from your property manager',subject,'messages')
        audit(conn,user['id'],c['company_id'],'sent','portal_message',mid,{'subject':subject,'thread_id':thread_id})
    return {'id':mid}


@router.post('/api/requests/{request_id}/respond')
def respond_request(request_id:int,data:dict,user=Depends(current_user)):
    response=text_field(data,'response')
    status=choice(data,'status',('in progress','resolved','declined'))
    with transaction() as conn:
        r=record(conn,'requests',request_id,user,('manager',))
        conn.execute('UPDATE requests SET response=?,status=?,responded_by=?,responded_at=? WHERE id=?',(response,status,user['id'],now(),request_id))
        insert(conn,'portal_messages',{'company_id':r['company_id'],'contract_id':r['contract_id'],'sender_id':user['id'],'subject':'Response: '+r['subject'],'body':response,'created_at':now()})
        notify_tenant(conn,r['contract_id'],'Your request has an update',r['subject'],'requests')
        audit(conn,user['id'],r['company_id'],'responded','request',request_id,{'status':status,'response':response})
    return {'ok':True}


@router.post('/api/requests/{request_id}/status')
def request_status(request_id:int,data:dict,user=Depends(current_user)):
    status=choice(data,'status',('open','in progress','resolved','declined'))
    with transaction() as conn:
        r=record(conn,'requests',request_id,user,('manager',))
        conn.execute('UPDATE requests SET status=?,responded_by=?,responded_at=? WHERE id=?',(status,user['id'],now(),request_id))
        notify_tenant(conn,r['contract_id'],'Your request has an update',r['subject'],'requests')
        audit(conn,user['id'],r['company_id'],'request_'+status.replace(' ','_'),'request',request_id,{'status':status})
    return {'ok':True}


@router.post('/api/repairs/{repair_id}/update')
def repair_update(repair_id:int,data:dict,user=Depends(current_user)):
    message=text_field(data,'message')
    with transaction() as conn:
        r=record(conn,'repairs',repair_id,user,OPS)
        visible=data.get('tenant_visible') is True
        doc_id=integer(data,'document_id') if data.get('document_id') else None
        if doc_id:
            doc=linked(conn,'documents',doc_id,r['company_id'])
            if doc['contract_id'] and doc['contract_id']!=r['contract_id']:
                raise HTTPException(422,'This document belongs to another tenancy.')
            if visible:
                share_document(conn,r['company_id'],r['contract_id'],doc_id,user['id'])
        uid=insert(conn,'repair_updates',{'company_id':r['company_id'],'repair_id':repair_id,'user_id':user['id'],'message':message,'document_id':doc_id,'tenant_visible':int(visible),'created_at':now()})
        sla.mark_responded(conn,repair_id)
        if visible:
            notify_tenant(conn,r['contract_id'],'Repair update',r['title'],'repairs')
        audit(conn,user['id'],r['company_id'],'updated','repair',repair_id,{'update_id':uid,'tenant_visible':visible})
    return {'id':uid}


@router.post('/api/contractors')
def add_contractor(data:dict,user=Depends(current_user)):
    company_id=integer(data,'company_id')
    with transaction() as conn:
        authorize(conn,user,company_id,OPS)
        doc_id=integer(data,'agreement_document_id') if data.get('agreement_document_id') else None
        if doc_id:
            linked(conn,'documents',doc_id,company_id)
        email=text_field(data,'email',maximum=254)
        if '@' not in email:
            raise HTTPException(422,'Enter a valid email.')
        cid=insert(conn,'contractors',{'company_id':company_id,'name':text_field(data,'name',maximum=120),'email':email,'phone':str(data.get('phone',''))[:50],'trade':text_field(data,'trade',maximum=120),'agreement_document_id':doc_id,'created_at':now()})
        audit(conn,user['id'],company_id,'created','contractor',cid,{})
    return {'id':cid}


@router.post('/api/contractors/{contractor_id}/agreement')
def attach_contractor_agreement(contractor_id:int,data:dict,user=Depends(current_user)):
    with transaction() as conn:
        contractor=record(conn,'contractors',contractor_id,user,OPS)
        doc_id=integer(data,'agreement_document_id')
        doc=linked(conn,'documents',doc_id,contractor['company_id'])
        if doc['kind']!='Contractor agreement':
            raise HTTPException(422,'Choose a document categorized as Contractor agreement.')
        conn.execute('UPDATE contractors SET agreement_document_id=? WHERE id=?',(doc_id,contractor_id))
        audit(conn,user['id'],contractor['company_id'],'attached','contractor_agreement',contractor_id,{'document_id':doc_id})
    return {'id':contractor_id,'agreement_document_id':doc_id}


@router.post('/api/contractors/agreements/{submission_id}/review')
def review_contractor_agreement(submission_id:int,data:dict,user=Depends(current_user)):
    decision=choice(data,'decision',('approved','rejected'))
    note=str(data.get('note','')).strip()[:1000]
    with transaction() as conn:
        submission=record(conn,'contractor_agreement_submissions',submission_id,user,('manager','property'))
        if submission['status']!='pending':
            raise HTTPException(409,'This agreement has already been reviewed.')
        conn.execute('UPDATE contractor_agreement_submissions SET status=?,reviewed_by=?,reviewed_at=?,review_note=? WHERE id=?',(decision,user['id'],now(),note,submission_id))
        if decision=='approved':
            conn.execute('UPDATE contractors SET agreement_document_id=? WHERE id=?',(submission['document_id'],submission['contractor_id']))
        ids=[r[0] for r in conn.execute('SELECT user_id FROM contractor_accounts WHERE contractor_id=? AND enabled=1',(submission['contractor_id'],))]
        notify(conn,submission['company_id'],ids,'Contractor agreement '+decision,note or ('Your agreement was approved.' if decision=='approved' else 'Please contact the property manager about your agreement.'),'agreement')
        audit(conn,user['id'],submission['company_id'],'agreement_'+decision,'contractor_agreement',submission_id,{'contractor_id':submission['contractor_id'],'document_id':submission['document_id'],'note':note})
    return {'ok':True}


def appointment(data):
    raw=data.get('scheduled_for')
    if not raw:
        return None
    try:
        dt=datetime.fromisoformat(raw)
        if dt.date()<local_today():
            raise ValueError()
        return dt.isoformat(timespec='minutes')
    except (ValueError,TypeError):
        raise HTTPException(422,'Choose a valid appointment date and time, today or later.')


@router.post('/api/work-orders')
def create_work_order(data:dict,user=Depends(current_user)):
    with transaction() as conn:
        r=record(conn,'repairs',integer(data,'repair_id'),user,OPS)
        c=linked(conn,'contractors',integer(data,'contractor_id'),r['company_id'])
        if not c['active'] or not c['agreement_document_id']:
            raise HTTPException(422,'Attach the contractor agreement before assigning tenant work.')
        scheduled=appointment(data)
        wid=insert(conn,'work_orders',{'company_id':r['company_id'],'repair_id':r['id'],'contractor_id':c['id'],'scope':text_field(data,'scope'),'scheduled_for':scheduled,'created_by':user['id'],'created_at':now()})
        conn.execute('UPDATE repairs SET status="planned",assigned_to=? WHERE id=?',(c['name'],r['id']))
        sla.mark_responded(conn,r['id'])
        ids=[x[0] for x in conn.execute('SELECT user_id FROM contractor_accounts WHERE contractor_id=? AND enabled=1',(c['id'],))]
        notify(conn,r['company_id'],ids,'New work order',r['title'],'jobs')
        notify_tenant(conn,r['contract_id'],'A contractor has been assigned',r['title']+(' · '+scheduled if scheduled else ''),'repairs')
        audit(conn,user['id'],r['company_id'],'assigned','work_order',wid,{'contractor_id':c['id']})
    return {'id':wid}


@router.get('/api/contractor/data')
def contractor_data(user=Depends(current_user)):
    with connect() as conn:
        accounts=[dict(r) for r in conn.execute('SELECT c.id,c.name,c.trade,c.company_id,co.name company_name FROM contractor_accounts a JOIN contractors c ON c.id=a.contractor_id JOIN companies co ON co.id=c.company_id WHERE a.user_id=? AND a.enabled=1',(user['id'],))]
        if not accounts:
            raise HTTPException(403,'Contractor access is not enabled.')
        agreements=[]
        for account in accounts:
            latest=conn.execute('SELECT s.id,s.status,s.review_note,s.submitted_at,s.reviewed_at,d.name document_name FROM contractor_agreement_submissions s JOIN documents d ON d.id=s.document_id WHERE s.contractor_id=? ORDER BY s.id DESC LIMIT 1',(account['id'],)).fetchone()
            agreements.append({'contractor_id':account['id'],'company_name':account['company_name'],'status':latest['status'] if latest else ('approved' if conn.execute('SELECT agreement_document_id FROM contractors WHERE id=?',(account['id'],)).fetchone()[0] else 'not_submitted'),'review_note':latest['review_note'] if latest else '', 'submitted_at':latest['submitted_at'] if latest else None,'document_name':latest['document_name'] if latest else None})
        # Explicit allowlist: never join invoices, ledger, deposits or private communications.
        jobs=[dict(r) for r in conn.execute('SELECT w.*,r.title,r.category,r.urgency,r.description,p.address,p.city,p.postcode,t.name tenant_name,t.phone tenant_phone FROM work_orders w JOIN contractor_accounts a ON a.contractor_id=w.contractor_id AND a.company_id=w.company_id JOIN repairs r ON r.id=w.repair_id JOIN contracts ct ON ct.id=r.contract_id JOIN properties p ON p.id=ct.property_id JOIN tenants t ON t.id=ct.tenant_id WHERE a.user_id=? AND a.enabled=1 ORDER BY w.id DESC',(user['id'],))]
        for company_id in {a['company_id'] for a in accounts}:
            db.audit_view(user['id'],company_id,'contractor_portal',None)
        return {'accounts':accounts,'jobs':jobs,'agreements':agreements,'today':local_today().isoformat()}


@router.post('/api/contractor/agreement')
async def contractor_submit_agreement(file:UploadFile=File(...),user=Depends(current_user)):
    with connect() as conn:
        accounts=[dict(r) for r in conn.execute('SELECT c.id,c.company_id FROM contractor_accounts a JOIN contractors c ON c.id=a.contractor_id WHERE a.user_id=? AND a.enabled=1',(user['id'],))]
        if not accounts:
            raise HTTPException(403,'Contractor access is not enabled.')
    if len(accounts)!=1:
        raise HTTPException(409,'Contact your property manager to submit an agreement for the correct company.')
    account=accounts[0]
    upload=await read_upload(file)
    with transaction() as conn:
        document_id=save_document(conn,account['company_id'],None,user['id'],'Contractor agreement',upload)
        submission_id=insert(conn,'contractor_agreement_submissions',{'company_id':account['company_id'],'contractor_id':account['id'],'document_id':document_id,'status':'pending','submitted_at':now(),'review_note':''})
        notify_staff(conn,account['company_id'],'Contractor agreement awaiting review',f'Agreement submitted by contractor #{account["id"]}.','contractors')
        audit(conn,user['id'],account['company_id'],'submitted','contractor_agreement',submission_id,{'document_id':document_id})
    return {'id':submission_id,'status':'pending'}


@router.post('/api/contractor/jobs/{job_id}/update')
def contractor_update(job_id:int,data:dict,user=Depends(current_user)):
    status=choice(data,'status',('accepted','scheduled','in progress','no access'))
    note=text_field(data,'note')
    with transaction() as conn:
        w=contractor_job(conn,user,job_id)
        if w['status'] in ('closed','completed','cancelled'):
            raise HTTPException(409,'This work order is no longer open for updates.')
        scheduled=appointment(data) or w['scheduled_for']
        if status=='scheduled' and not scheduled:
            raise HTTPException(422,'Choose an appointment date.')
        conn.execute('UPDATE work_orders SET status=?,scheduled_for=? WHERE id=?',(status,scheduled,job_id))
        r=dict(conn.execute('SELECT * FROM repairs WHERE id=?',(w['repair_id'],)).fetchone())
        message=f'Contractor update: {status}. '+note+(f' Appointment: {scheduled}' if scheduled else '')
        insert(conn,'repair_updates',{'company_id':w['company_id'],'repair_id':w['repair_id'],'user_id':user['id'],'message':message,'tenant_visible':1,'created_at':now()})
        notify_tenant(conn,r['contract_id'],'Contractor update',message,'repairs')
        notify_staff(conn,w['company_id'],'Work order updated',r['title'],'contractors')
        audit(conn,user['id'],w['company_id'],'updated','work_order',job_id,{'status':status,'scheduled_for':scheduled,'note':note})
    return {'ok':True}


@router.post('/api/contractor/jobs/{job_id}/quote')
def contractor_quote(job_id:int,data:dict,user=Depends(current_user)):
    amount=money(data,'amount')
    note=text_field(data,'note')
    with transaction() as conn:
        w=contractor_job(conn,user,job_id)
        if w['status'] in ('closed','completed','cancelled') or w['quote_status']=='approved':
            raise HTTPException(409,'This work order cannot accept a new quote.')
        conn.execute('UPDATE work_orders SET quote_cents=?,quote_note=?,quote_status="pending",quoted_by=? WHERE id=?',(amount,note,user['id'],job_id))
        notify_staff(conn,w['company_id'],'Quote awaiting approval',f'Work order #{job_id}: {euros(amount)}','contractors')
        audit(conn,user['id'],w['company_id'],'quoted','work_order',job_id,{'amount_cents':amount,'note':note})
    return {'ok':True}


@router.post('/api/work-orders/{job_id}/quote-review')
def quote_review(job_id:int,data:dict,user=Depends(current_user)):
    decision=choice(data,'decision',('approved','rejected'))
    note=text_field(data,'note')
    with transaction() as conn:
        w=record(conn,'work_orders',job_id,user,('manager',))
        if w['quote_status']!='pending':
            raise HTTPException(409,'There is no pending quote.')
        if w['quoted_by']==user['id']:
            raise HTTPException(403,'A different person must review the quote.')
        conn.execute('UPDATE work_orders SET quote_status=?,approved_by=? WHERE id=?',(decision,user['id'],job_id))
        ids=[x[0] for x in conn.execute('SELECT user_id FROM contractor_accounts WHERE contractor_id=? AND enabled=1',(w['contractor_id'],))]
        notify(conn,w['company_id'],ids,'Quote '+decision,note,'jobs')
        audit(conn,user['id'],w['company_id'],'quote_'+decision,'work_order',job_id,{'note':note})
    return {'ok':True}


@router.post('/api/contractor/jobs/{job_id}/complete')
async def contractor_complete(job_id:int,note:str=Form(...),file:UploadFile=File(...),invoice:UploadFile|None=File(None),user=Depends(current_user)):
    note=text_field({'note':note},'note')
    with connect() as conn:
        contractor_job(conn,user,job_id)
    upload=await read_upload(file)
    invoice_upload=await read_upload(invoice) if invoice and invoice.filename else None
    with transaction() as conn:
        w=contractor_job(conn,user,job_id)
        if w['status'] in ('closed','completed','cancelled'):
            raise HTTPException(409,'This work order has already been completed or closed.')
        if w['quote_status'] in ('pending','rejected'):
            raise HTTPException(409,'Resolve the quote approval before submitting completed work.')
        r=dict(conn.execute('SELECT * FROM repairs WHERE id=?',(w['repair_id'],)).fetchone())
        did=save_document(conn,w['company_id'],r['contract_id'],user['id'],'Repair completion evidence',upload)
        invoice_id=save_document(conn,w['company_id'],r['contract_id'],user['id'],'supplier invoice',invoice_upload) if invoice_upload else None
        conn.execute('UPDATE work_orders SET status="completed",completion_note=?,completion_document_id=?,invoice_document_id=?,completed_at=? WHERE id=?',(note,did,invoice_id,now(),job_id))
        # Contractor completion does not close the tenant's repair until staff reviews it.
        task(conn,w['company_id'],r['contract_id'],f'Verify completed work order #{job_id}',local_today().isoformat(),'Repair')
        notify_staff(conn,w['company_id'],'Completed work needs review',r['title'],'contractors')
        audit(conn,user['id'],w['company_id'],'completion_submitted','work_order',job_id,{'document_id':did,'invoice_document_id':invoice_id})
    return {'ok':True}


@router.post('/api/work-orders/{job_id}/close')
def close_work_order(job_id:int,data:dict,user=Depends(current_user)):
    note=text_field(data,'note')
    with transaction() as conn:
        w=record(conn,'work_orders',job_id,user,OPS)
        if w['status']!='completed' or not w['completion_document_id']:
            raise HTTPException(409,'A contractor must first submit completion evidence.')
        if data.get('confirmed') is not True:
            raise HTTPException(422,'Confirm the work was checked.')
        r=dict(conn.execute('SELECT * FROM repairs WHERE id=?',(w['repair_id'],)).fetchone())
        conn.execute('UPDATE work_orders SET status="closed" WHERE id=?',(job_id,))
        conn.execute('UPDATE repairs SET status="done",completion_note=?,completion_document_id=?,completed_at=? WHERE id=?',(note,w['completion_document_id'],now(),r['id']))
        share_document(conn,w['company_id'],r['contract_id'],w['completion_document_id'],user['id'])
        insert(conn,'repair_updates',{'company_id':w['company_id'],'repair_id':r['id'],'user_id':user['id'],'message':'Work checked and completed. '+note,'document_id':w['completion_document_id'],'tenant_visible':1,'created_at':now()})
        notify_tenant(conn,r['contract_id'],'Your repair is complete',r['title'],'repairs')
        audit(conn,user['id'],w['company_id'],'closed','work_order',job_id,{'note':note})
    return {'ok':True}


@router.get('/api/contractor/jobs/{job_id}/documents/{document_id}')
def contractor_document(job_id:int,document_id:int,user=Depends(current_user)):
    with transaction() as conn:
        w=contractor_job(conn,user,job_id)
        if document_id not in (w['completion_document_id'],w['invoice_document_id']):
            raise HTTPException(404,'Document not found.')
        doc=dict(conn.execute('SELECT * FROM documents WHERE id=?',(document_id,)).fetchone())
        audit(conn,user['id'],w['company_id'],'downloaded','document',document_id,{})
    return FileResponse(db.DATA_DIR/'uploads'/doc['file_key'],media_type=doc['mime'],filename=doc['name'])
