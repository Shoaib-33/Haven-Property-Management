import calendar
from datetime import date, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException

from .clock import local_today
from .db import audit, now, transaction
from .security import current_user, linked, record
from .portal_access import notify_tenant
from .main import authorize, choice, insert, integer, invoice_balance, money, task, text_field

router=APIRouter()


@router.post('/api/credit-notes')
def propose_credit(data:dict,user=Depends(current_user)):
    with transaction() as conn:
        inv=record(conn,'invoices',integer(data,'invoice_id'),user,('finance','manager'))
        amount=money(data,'amount')
        if amount>invoice_balance(conn,inv['id']):
            raise HTTPException(409,'A credit note cannot exceed the outstanding balance.')
        cid=insert(conn,'credit_notes',{'company_id':inv['company_id'],'invoice_id':inv['id'],'amount_cents':amount,'reason':text_field(data,'reason'),'created_by':user['id'],'created_at':now()})
        audit(conn,user['id'],inv['company_id'],'proposed','credit_note',cid,{'invoice_id':inv['id'],'amount_cents':amount})
    return {'id':cid}


@router.post('/api/credit-notes/{credit_id}/review')
def review_credit(credit_id:int,data:dict,user=Depends(current_user)):
    decision=choice(data,'decision',('approved','rejected'))
    note=text_field(data,'note')
    with transaction() as conn:
        credit=record(conn,'credit_notes',credit_id,user,('manager',))
        if credit['status']!='pending':
            raise HTTPException(409,'This credit note was already reviewed.')
        if credit['created_by']==user['id']:
            raise HTTPException(403,'A different person must approve the credit note.')
        if decision=='approved':
            inv=linked(conn,'invoices',credit['invoice_id'],credit['company_id'])
            if credit['amount_cents']>invoice_balance(conn,inv['id']):
                raise HTTPException(409,'The remaining invoice balance changed; this credit no longer fits.')
            insert(conn,'ledger',{'company_id':credit['company_id'],'contract_id':inv['contract_id'],'invoice_id':inv['id'],'kind':'credit_note','amount_cents':-credit['amount_cents'],'created_by':user['id'],'created_at':now(),'note':f'Credit note #{credit_id}: {note}'})
            notify_tenant(conn,inv['contract_id'],'Invoice adjustment approved',f'Credit of {credit["amount_cents"] / 100:.2f} has been applied to invoice {inv["number"]}.','payments')
        conn.execute('UPDATE credit_notes SET status=?,reviewed_by=?,review_note=?,reviewed_at=? WHERE id=?',(decision,user['id'],note,now(),credit_id))
        audit(conn,user['id'],credit['company_id'],decision,'credit_note',credit_id,{'note':note})
    return {'ok':True}


@router.post('/api/payment-plans')
def propose_plan(data:dict,user=Depends(current_user)):
    with transaction() as conn:
        c=record(conn,'contracts',integer(data,'contract_id'),user,('finance','manager'))
        first=data.get('first_due','')
        try:
            due=date.fromisoformat(first)
            if due<=local_today(): raise ValueError()
        except (ValueError,TypeError):
            raise HTTPException(422,'The first instalment needs a future due date.')
        total=money(data,'total')
        current=conn.execute('SELECT i.* FROM invoices i WHERE contract_id=?',(c['id'],)).fetchall()
        overdue=sum(max(0,invoice_balance(conn,r['id'])) for r in current if r['due_date']<local_today().isoformat())
        if total>overdue or overdue==0:
            raise HTTPException(409,'The arrangement must cover an existing overdue balance and cannot exceed it.')
        every=integer(data,'interval_days')
        if every<7 or every>90:
            raise HTTPException(422,'Choose an instalment interval between 7 and 90 days.')
        amount=money(data,'instalment')
        if amount>total:
            amount=total
        pid=insert(conn,'payment_plans',{'company_id':c['company_id'],'contract_id':c['id'],'title':text_field(data,'title'),'total_cents':total,'instalment_cents':amount,'first_due':first,'interval_days':every,'created_by':user['id'],'created_at':now()})
        task(conn,c['company_id'],c['id'],f'Review proposed payment plan #{pid}',local_today().isoformat(),'Payment plan','Finance team')
        audit(conn,user['id'],c['company_id'],'proposed','payment_plan',pid,{'total_cents':total})
    return {'id':pid}


@router.post('/api/payment-plans/{plan_id}/review')
def review_plan(plan_id:int,data:dict,user=Depends(current_user)):
    decision=choice(data,'decision',('approved','rejected'))
    note=text_field(data,'note')
    with transaction() as conn:
        p=record(conn,'payment_plans',plan_id,user,('manager',))
        if p['status']!='pending': raise HTTPException(409,'This plan was already reviewed.')
        if p['created_by']==user['id']: raise HTTPException(403,'A different person must approve the payment plan.')
        if decision=='approved':
            outstanding=sum(max(0,invoice_balance(conn,r['id'])) for r in conn.execute('SELECT * FROM invoices WHERE contract_id=? AND due_date<?',(p['contract_id'],local_today().isoformat())))
            if p['total_cents']>outstanding: raise HTTPException(409,'The overdue balance changed. Create a revised plan.')
            remaining=p['total_cents']; due=date.fromisoformat(p['first_due'])
            while remaining:
                amount=min(remaining,p['instalment_cents'])
                insert(conn,'plan_instalments',{'company_id':p['company_id'],'plan_id':p['id'],'due_date':due.isoformat(),'amount_cents':amount})
                remaining-=amount;due+=timedelta(days=p['interval_days'])
        conn.execute('UPDATE payment_plans SET status=?,approved_by=? WHERE id=?',(decision,user['id'],plan_id))
        notify_tenant(conn,p['contract_id'],'Payment arrangement '+decision,note,'payments')
        audit(conn,user['id'],p['company_id'],decision,'payment_plan',plan_id,{'note':note})
    return {'ok':True}


@router.post('/api/payment-plans/{plan_id}/apply-proof')
def apply_proof(plan_id:int,data:dict,user=Depends(current_user)):
    proof_id=integer(data,'proof_id')
    installment_id=integer(data,'instalment_id')
    note=text_field(data,'note')
    with transaction() as conn:
        plan=record(conn,'payment_plans',plan_id,user,('finance','manager'))
        if plan['status']!='approved':raise HTTPException(409,'The payment plan must be approved first.')
        item=conn.execute('SELECT * FROM plan_instalments WHERE id=? AND plan_id=? AND company_id=?',(installment_id,plan_id,plan['company_id'])).fetchone()
        if not item or item['status']!='open':raise HTTPException(409,'This instalment is not open.')
        proof=record(conn,'payment_proofs',proof_id,user,('finance','manager'))
        if proof['status']!='approved':raise HTTPException(409,'Only an approved receipt can fulfil an instalment.')
        inv=linked(conn,'invoices',proof['invoice_id'],plan['company_id'])
        if inv['contract_id']!=plan['contract_id']:raise HTTPException(422,'The payment belongs to another tenancy.')
        already=conn.execute('SELECT 1 FROM plan_instalments WHERE payment_proof_id=?',(proof_id,)).fetchone()
        if already:raise HTTPException(409,'This receipt is already attached to an instalment.')
        if proof['amount_cents']<item['amount_cents']:raise HTTPException(422,'This receipt is smaller than the instalment. Submit another receipt for the balance.')
        conn.execute('UPDATE plan_instalments SET status="paid",payment_proof_id=?,paid_at=? WHERE id=?',(proof_id,now(),installment_id))
        audit(conn,user['id'],plan['company_id'],'instalment_paid','payment_plan',plan_id,{'instalment_id':installment_id,'proof_id':proof_id,'note':note})
    return {'ok':True}


@router.post('/api/settlements/{settlement_id}/publish')
def publish_statements(settlement_id:int,data:dict,user=Depends(current_user)):
    confirmation=data.get('confirm_individual_statements') is True
    if not confirmation:raise HTTPException(422,'Confirm each statement contains only its tenant’s allocated costs and advances.')
    import json
    from .portal_access import share_document, notify_tenant
    from .main import save_document, service_charge_statement_pdf_bytes
    with transaction() as conn:
        settlement=record(conn,'settlements',settlement_id,user,('manager',))
        if settlement['status']!='approved':raise HTTPException(409,'Approve the settlement before publishing tenant statements.')
        snapshot=json.loads(settlement['snapshot'])
        result=[]
        for line in snapshot['lines']:
            existing=conn.execute('SELECT id FROM published_statements WHERE settlement_id=? AND contract_id=?',(settlement_id,line['contract_id'])).fetchone()
            if existing:continue
            total=0;period_days=366 if calendar.isleap(settlement['year']) else 365
            allocations=line.get('costs')
            if allocations is None:  # Preserve publishing for settlements drafted before allocation snapshots were introduced.
                allocations=[{**c,'allocated_cents':int((Decimal(c['amount_cents'])*Decimal(line['days'])/period_days).quantize(Decimal('1'))),'allocation_method':'tenancy_days','unit_weight':1,'occupied_days':line['days'],'year_days':period_days} for c in snapshot['costs']]
            for c in allocations:
                total+=c['allocated_cents']
            advance=line['advance_cents'];balance=total-advance
            tenant=conn.execute('SELECT t.name,p.address,co.name company FROM contracts ct JOIN tenants t ON t.id=ct.tenant_id JOIN properties p ON p.id=ct.property_id JOIN companies co ON co.id=ct.company_id WHERE ct.id=?',(line['contract_id'],)).fetchone()
            filename=f'service-charge-{settlement["year"]}-{line["contract_id"]}.pdf'
            company_row=conn.execute('SELECT name,address,kvk,payment_instructions FROM companies WHERE id=?',(settlement['company_id'],)).fetchone()
            prop_row=conn.execute('SELECT p.name,p.address,p.city FROM contracts ct JOIN properties p ON p.id=ct.property_id WHERE ct.id=?',(line['contract_id'],)).fetchone()
            from .invoice_tools import logo_bytes
            pdf=service_charge_statement_pdf_bytes(tenant['company'],tenant['name'],tenant['address'],settlement['year'],line['days'],period_days,allocations,total,advance,balance,filename,
                company=dict(company_row),property_label=f"{prop_row['name']}, {prop_row['address']}, {prop_row['city']}",reference=f"SA-{settlement['year']}-{settlement_id:03d}-{line['contract_id']:04d}",issued_on=local_today().isoformat(),logo=logo_bytes(settlement['company_id']))
            doc_id=save_document(conn,settlement['company_id'],line['contract_id'],user['id'],'Service-charge statement',(pdf,'application/pdf','.pdf',filename))
            sid=insert(conn,'published_statements',{'company_id':settlement['company_id'],'settlement_id':settlement_id,'contract_id':line['contract_id'],'document_id':doc_id,'published_by':user['id'],'published_at':now()})
            share_document(conn,settlement['company_id'],line['contract_id'],doc_id,user['id'])
            notify_tenant(conn,line['contract_id'],'Your annual service-charge statement is ready',f'{settlement["year"]} statement · {"payable" if balance>0 else "refundable"}: EUR {abs(balance)/100:,.2f}.','documents')
            result.append(sid)
        audit(conn,user['id'],settlement['company_id'],'statements_published','settlement',settlement_id,{'count':len(result)})
    return {'published':len(result)}
