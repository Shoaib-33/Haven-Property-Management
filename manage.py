"""Local administration commands. Run python manage.py --help."""
import argparse
import getpass
import io
import json
import os
from datetime import date, timedelta

from app import db
from app.clock import local_today
from app.db import audit, now, transaction
from app.security import password_hash

# the password of every demo login; set HAVEN_DEMO_PASSWORD (12+ characters) to use your own on a public demo
DEMO_PASSWORD = os.environ.get('HAVEN_DEMO_PASSWORD', '').strip() or 'HavenDemo!2026'


def demo_portal_accounts(conn, company_id, manager_id):
    from reportlab.pdfgen.canvas import Canvas
    from app.main import save_document
    contracts=conn.execute('SELECT c.*,t.name tenant_name FROM contracts c JOIN tenants t ON t.id=c.tenant_id WHERE c.company_id=? ORDER BY c.id',(company_id,)).fetchall()
    if not contracts:
        return
    c=contracts[0]
    if not conn.execute('SELECT 1 FROM portal_accounts WHERE tenant_id=?',(c['tenant_id'],)).fetchone():
        user_id=conn.execute('SELECT id FROM users WHERE email=?',('tenant@haven.local',)).fetchone()
        if user_id: user_id=user_id['id']
        else: user_id=conn.execute('INSERT INTO users(name,email,password_hash) VALUES(?,?,?)',(c['tenant_name'],'tenant@haven.local',password_hash(DEMO_PASSWORD))).lastrowid
        conn.execute('INSERT OR IGNORE INTO portal_accounts(user_id,tenant_id,company_id,created_at) VALUES(?,?,?,?)',(user_id,c['tenant_id'],company_id,now()))
    if conn.execute('SELECT 1 FROM work_orders WHERE company_id=?',(company_id,)).fetchone():
        return
    stream=io.BytesIO();pdf=Canvas(stream);pdf.drawString(72,760,'DEMO CONTRACTOR DATA PROCESSING AGREEMENT');pdf.drawString(72,735,'Fictional example for portal demonstration only.');pdf.save()
    doc_id=save_document(conn,company_id,None,manager_id,'Contractor agreement',(stream.getvalue(),'application/pdf','.pdf','demo-contractor-agreement.pdf'))
    contractor_id=conn.execute('INSERT INTO contractors(company_id,name,email,phone,trade,agreement_document_id,created_at) VALUES(?,?,?,?,?,?,?)',(company_id,'North Canal Repairs','contractor@haven.local','+31 20 000 0000','Plumbing & general repairs',doc_id,now())).lastrowid
    contractor_user=conn.execute('INSERT INTO users(name,email,password_hash) VALUES(?,?,?)',('North Canal Repairs','contractor@haven.local',password_hash(DEMO_PASSWORD))).lastrowid
    conn.execute('INSERT INTO contractor_accounts(user_id,contractor_id,company_id,created_at) VALUES(?,?,?,?)',(contractor_user,contractor_id,company_id,now()))
    repair=conn.execute('SELECT * FROM repairs WHERE company_id=? ORDER BY id LIMIT 1',(company_id,)).fetchone()
    if not repair:return
    conn.execute('INSERT INTO work_orders(company_id,repair_id,contractor_id,scope,scheduled_for,created_by,created_at) VALUES(?,?,?,?,?,?,?)',(company_id,repair['id'],contractor_id,'Inspect and repair the reported issue. Confirm the appointment with the resident before attending.',(local_today()+timedelta(days=2)).isoformat()+'T10:00',manager_id,now()))


def demo_owners(conn, company_id, creator_id):
    if conn.execute('SELECT 1 FROM owners WHERE company_id=?',(company_id,)).fetchone():
        return
    owner_id=conn.execute('INSERT INTO owners(company_id,name,email,phone,payout_details,created_at) VALUES(?,?,?,?,?,?)',(company_id,'Van Dijk Vastgoed BV','owner@haven.local','+31 20 000 0001','DEMO: paid manually by the property manager',now())).lastrowid
    pid=conn.execute('INSERT INTO portfolios(company_id,owner_id,name,fee_percent,created_at) VALUES(?,?,?,?,?)',(company_id,owner_id,'Amsterdam Canal Belt',6.0,now())).lastrowid
    conn.execute('UPDATE properties SET portfolio_id=? WHERE company_id=?',(pid,company_id))
    user=conn.execute('SELECT id FROM users WHERE email=?',('owner@haven.local',)).fetchone()
    uid=user['id'] if user else conn.execute('INSERT INTO users(name,email,password_hash) VALUES(?,?,?)',('Van Dijk Vastgoed BV','owner@haven.local',password_hash(DEMO_PASSWORD))).lastrowid
    conn.execute('INSERT INTO owner_accounts(user_id,owner_id,company_id,created_at) VALUES(?,?,?,?)',(uid,owner_id,company_id,now()))


def demo():
    from app.main import insert, issue_month, save_document, submit_proof, task
    from reportlab.pdfgen.canvas import Canvas
    db.init_db()
    today = local_today()
    start = date(today.year-1, 1, 1).isoformat()
    with transaction() as conn:
        if conn.execute('SELECT COUNT(*) FROM users').fetchone()[0]:
            fixture=conn.execute('SELECT id FROM companies WHERE kvk="DEMO-0001"').fetchone()
            manager=conn.execute('SELECT id FROM users WHERE email="manager@haven.local"').fetchone()
            if fixture and manager:
                demo_portal_accounts(conn,fixture['id'],manager['id'])
                demo_owners(conn,fixture['id'],manager['id'])
                audit(conn,manager['id'],fixture['id'],'demo_portals_ready','company',fixture['id'],{'fictional':True})
                print('Demo tenant and contractor portals are ready.')
                print('Tenant: tenant@haven.local · Contractor: contractor@haven.local')
                print('Demo password for both: ' + ('(the one in HAVEN_DEMO_PASSWORD)' if os.environ.get('HAVEN_DEMO_PASSWORD') else DEMO_PASSWORD))
            else:
                print('Database already initialized. No records changed.')
            return
        admin = insert(conn, 'users', {'name':'Alex Morgan', 'email':'admin@haven.local', 'password_hash':password_hash(DEMO_PASSWORD), 'global_admin':1})
        finance = insert(conn, 'users', {'name':'Sophie de Vries', 'email':'finance@haven.local', 'password_hash':password_hash(DEMO_PASSWORD)})
        manager = insert(conn, 'users', {'name':'Daan Bakker', 'email':'manager@haven.local', 'password_hash':password_hash(DEMO_PASSWORD)})
        company = insert(conn, 'companies', {'name':'Haven Residential BV','kvk':'DEMO-0001','address':'Keizersgracht 128, Amsterdam','payment_instructions':'DEMO ONLY. Contact your property manager for the agreed payment instructions. Include your invoice number as the reference.','created_at':now()})
        other = insert(conn, 'companies', {'name':'Canal House BV','kvk':'DEMO-0002','address':'Rotterdam','payment_instructions':'Contact the property manager for payment details.','created_at':now()})
        for uid, cid, role in [(admin,company,'admin'),(admin,other,'admin'),(finance,company,'finance'),(manager,company,'manager')]:
            conn.execute('INSERT INTO memberships VALUES(?,?,?)',(uid,cid,role))
        people = [('Emma Janssen','emma@example.com'),('Noah de Groot','noah@example.com'),('Olivia Visser','olivia@example.com'),('Liam Smit','liam@example.com'),('Mila van Dijk','mila@example.com')]
        homes = [('The Linden · 2A','Lindengracht 42 A','Amsterdam',68,'B'),('Canal View · 1B','Prinsengracht 216 B','Amsterdam',82,'A'),('Parkside · 3C','Vondelstraat 93 C','Amsterdam',54,'C'),('The Jordaan · 4A','Elandsgracht 18 A','Amsterdam',76,'A'),('Westhaven · 2B','Spaarndammerstraat 52 B','Amsterdam',61,'B'),('Garden House · 1','Rozenstraat 74','Amsterdam',96,'A+')]
        contracts=[]
        for i,h in enumerate(homes):
            pid=insert(conn,'properties',{'company_id':company,'name':h[0],'address':h[1],'city':h[2],'postcode':'1015 AA','kind':'Apartment','area':h[3],'rooms':2+i%3,'energy_label':h[4],'created_at':now()})
            if i>=len(people):
                continue
            tid=insert(conn,'tenants',{'company_id':company,'name':people[i][0],'email':people[i][1],'phone':'+31 6 0000 0000','created_at':now()})
            basic=[112500,145000,97500,132500,107500][i]
            cid=insert(conn,'contracts',{'company_id':company,'property_id':pid,'tenant_id':tid,'signed_on':start,'start_date':start,'deposit_cents':basic*2,'created_at':now()})
            insert(conn,'rent_versions',{'company_id':company,'contract_id':cid,'effective_from':start,'basic_cents':basic,'service_cents':7500,'created_at':now(),'created_by':admin})
            contracts.append(cid)
            insert(conn,'deposit_events',{'company_id':company,'contract_id':cid,'kind':'received','amount_cents':basic*2,'status':'approved','reason':'Fictional opening balance for demonstration','created_by':admin,'created_at':now()})
        issue_month(conn,company,today.strftime('%Y-%m'),admin)
        invoices=conn.execute('SELECT * FROM invoices WHERE company_id=? ORDER BY id',(company,)).fetchall()
        for i,inv in enumerate(invoices[:4]):
            stream=io.BytesIO()
            pdf=Canvas(stream)
            pdf.drawString(72,780,'FICTIONAL PAYMENT RECEIPT - DEMO ONLY')
            pdf.drawString(72,750,f'Invoice: {inv["number"]}')
            pdf.drawString(72,725,f'Reference: DEMO-RECEIPT-{i+1}')
            pdf.drawString(72,700,f'Amount: EUR {inv["total_cents"]/100:.2f}')
            pdf.save()
            amount=inv['total_cents'] if i!=2 else 50000
            proof=submit_proof(conn,dict(inv),{'amount':str(amount/100),'paid_on':today.isoformat(),'method':'Bank transfer','reference':f'DEMO-RECEIPT-{i+1}','note':'Fictional receipt for testing the review workflow.'},(stream.getvalue(),'application/pdf','.pdf',f'demo-receipt-{i+1}.pdf'),None)
            if i in (1,2):
                conn.execute('UPDATE payment_proofs SET status="approved",reviewed_by=?,review_note="Fictional demo payment",reviewed_at=? WHERE id=?',(manager,now(),proof['id']))
                insert(conn,'ledger',{'company_id':company,'contract_id':inv['contract_id'],'invoice_id':inv['id'],'proof_id':proof['id'],'kind':'payment','amount_cents':-amount,'created_by':manager,'created_at':now(),'note':'Fictional demo payment'})
                audit(conn,manager,company,'approved','payment_proof',proof['id'],{'demo':True})
        repair_data=[('Kitchen tap leaking','Plumbing','Routine','Slow leak under the kitchen sink. Tenant supplied a written report.',8,'planned','J. van Leeuwen'),('Bedroom damp patch','Damp / mould','Urgent','Moisture near the outside wall. Arrange an inspection and document findings.',36,'in progress','Daan Bakker'),('Hallway light fitting','Electrical','Routine','Communal entrance light flickers intermittently.',3,'received','')]
        for i,r in enumerate(repair_data):
            reported=today-timedelta(days=r[4])
            rid=insert(conn,'repairs',{'company_id':company,'contract_id':contracts[i],'title':r[0],'category':r[1],'urgency':r[2],'description':r[3],'reported_on':reported.isoformat(),'due_date':(reported+timedelta(days=42)).isoformat(),'status':r[5],'assigned_to':r[6],'created_at':now()})
            task(conn,company,contracts[i],f'Review repair #{rid}: {r[0]}',(reported+timedelta(days=35)).isoformat(),'Repair',r[6] or 'Property team')
        task(conn,company,contracts[0],'Review the two submitted payment receipts',today.isoformat(),'Payments','Sophie de Vries')
        task(conn,company,contracts[3],'Check signed move-in inspection report',(today+timedelta(days=2)).isoformat(),'Inspection','Daan Bakker')
        task(conn,company,None,'Review current rent reference tables',(today+timedelta(days=5)).isoformat(),'Compliance','Alex Morgan')
        insert(conn,'communications',{'company_id':company,'contract_id':contracts[0],'channel':'Phone','direction':'Inbound','subject':'Repair appointment confirmed','body':'Tenant confirmed availability for the plumber on Thursday afternoon. Follow up with written confirmation.','status':'Recorded','created_by':manager,'created_at':now()})
        demo_portal_accounts(conn,company,manager)
        demo_owners(conn,company,manager)
        audit(conn,admin,company,'demo_initialized','company',company,{'fictional':True})
    print('Fictional demonstration data created.')
    print('Accounts: admin@haven.local, finance@haven.local, manager@haven.local')
    print('Tenant: tenant@haven.local · Contractor: contractor@haven.local · Owner: owner@haven.local')
    print('Demo password for all three: ' + ('(the one in HAVEN_DEMO_PASSWORD)' if os.environ.get('HAVEN_DEMO_PASSWORD') else DEMO_PASSWORD))


def create_admin():
    db.init_db()
    name=input('Name: ').strip()
    email=input('Email: ').strip().lower()
    password=getpass.getpass('Password (12+ characters): ')
    if len(password)<12 or not name or '@' not in email:
        raise SystemExit('A name, valid email, and password of at least 12 characters are required.')
    with transaction() as conn:
        conn.execute('INSERT INTO users(name,email,password_hash,global_admin) VALUES(?,?,?,1)',(name,email,password_hash(password)))
    print('Administrator created. Sign in and create your first company.')


def main():
    parser=argparse.ArgumentParser(description='Haven local administration')
    parser.add_argument('command',choices=['demo','init','create-admin','backup'])
    args=parser.parse_args()
    if args.command=='demo': demo()
    elif args.command=='init': db.init_db(); print('Database initialized.')
    elif args.command=='create-admin': create_admin()
    elif args.command=='backup':
        import sqlite3
        import zipfile
        from datetime import datetime
        db.init_db()
        destination=db.DATA_DIR / ('backup-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'.zip')
        snapshot=db.DATA_DIR / 'backup-snapshot.db'
        with db.connect() as source,sqlite3.connect(snapshot) as target:
            source.backup(target)
        with zipfile.ZipFile(destination,'w',zipfile.ZIP_DEFLATED) as archive:
            archive.write(snapshot,'haven.db')
            for path in (db.DATA_DIR/'uploads').iterdir():
                if path.is_file(): archive.write(path,'uploads/'+path.name)
        snapshot.unlink()
        print('Backup created:',destination)


if __name__=='__main__': main()
