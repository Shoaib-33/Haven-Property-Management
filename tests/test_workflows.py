import contextlib
import hashlib
import io
import os
import sqlite3
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import db
from app.main import app, invoice_pdf_bytes
from manage import demo


class WorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original_dir = db.DATA_DIR

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        db.DATA_DIR = Path(self.temp.name)
        with contextlib.redirect_stdout(io.StringIO()):
            demo()
        self.client = TestClient(app)
        self.login('admin')
        self.data = self.client.get('/api/data?company_id=1').json()

    def tearDown(self):
        self.client.close()
        db.DATA_DIR = self.original_dir
        self.temp.cleanup()

    def login(self, role):
        response = self.client.post('/api/login', json={'email': f'{role}@haven.local', 'password': 'HavenDemo!2026'})
        self.assertEqual(response.status_code, 200, response.text)
        self.csrf = response.json()['csrf']
        self.client.headers['X-CSRF-Token'] = self.csrf

    def post(self, path, data):
        return self.client.post(path, json=data)

    def link(self, invoice_id):
        r = self.post(f'/api/invoices/{invoice_id}/link', {})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()['path'].split('/')[-1]

    def submit(self, token, amount='100.00', reference='NEW-REF-1', content=b'%PDF-1.4\nTest proof payload'):
        return self.client.post(f'/api/public/{token}/proof', data={'amount':amount,'paid_on':date.today().isoformat(),'method':'Bank transfer','reference':reference}, files={'file':('receipt.pdf',content,'application/pdf')})

    def test_login_csrf_and_company_isolation(self):
        self.assertEqual(self.client.get('/api/health').json()['status'], 'ok')
        self.assertEqual(self.client.post('/api/invoices/run', headers={'X-CSRF-Token':'wrong'}, json={'company_id':1,'period':date.today().strftime('%Y-%m')}).status_code,403)
        self.login('finance')
        self.assertEqual(self.client.get('/api/data?company_id=2').status_code,403)
        self.assertEqual(self.post('/api/companies',{'name':'Forbidden'}).status_code,403)
        self.assertEqual(self.client.post('/api/logout',headers={'Origin':'https://evil.example'}).status_code,403)

    def test_invoice_run_is_idempotent(self):
        result = self.post('/api/invoices/run',{'company_id':1,'period':date.today().strftime('%Y-%m')})
        self.assertEqual(result.json()['created'],0)

    def test_tenant_contact_can_be_archived_without_deleting_history(self):
        tenant=self.post('/api/records/tenants',{'company_id':1,'name':'Unassigned contact','email':'unassigned@example.com'}).json()
        archived=self.post(f"/api/tenants/{tenant['id']}/archive",{'company_id':1})
        self.assertEqual(archived.status_code,200,archived.text)
        contact=next(t for t in self.client.get('/api/data?company_id=1').json()['tenants'] if t['id']==tenant['id'])
        self.assertIsNotNone(contact['archived_at'])
        restored=self.post(f"/api/tenants/{tenant['id']}/restore",{'company_id':1})
        self.assertEqual(restored.status_code,200,restored.text)
        contact=next(t for t in self.client.get('/api/data?company_id=1').json()['tenants'] if t['id']==tenant['id'])
        self.assertIsNone(contact['archived_at'])

    def test_active_tenant_cannot_be_archived(self):
        tenant=self.data['contracts'][0]['tenant_id']
        response=self.post(f'/api/tenants/{tenant}/archive',{'company_id':1})
        self.assertEqual(response.status_code,409,response.text)

    def test_activation_sign_in_uses_invited_tenant_identity(self):
        tenant=self.data['contracts'][0]['tenant_id']
        invitation=self.post('/api/accounts/invite',{'kind':'tenant','target_id':tenant})
        self.assertEqual(invitation.status_code,200,invitation.text)
        token=invitation.json()['path'].split('/')[-1]
        password='TenantAccess!2026'
        activated=self.client.post(f'/api/invitations/{token}',json={'password':password})
        self.assertEqual(activated.status_code,200,activated.text)
        signed_in=self.client.post('/api/login',headers={'X-Haven-Portal':'tenant'},json={'email':activated.json()['email'],'password':password})
        self.assertEqual(signed_in.status_code,200,signed_in.text)
        identity=self.client.get('/api/me',headers={'X-Haven-Portal':'tenant'}).json()
        self.assertEqual(identity['name'],next(t['name'] for t in self.data['tenants'] if t['id']==tenant))
        self.assertTrue(identity['tenant_access'])

    def test_tenant_request_appears_in_landlord_inbox_and_sets_inbox_alert(self):
        contract=self.data['contracts'][0]
        invitation=self.post('/api/accounts/invite',{'kind':'tenant','target_id':contract['tenant_id']})
        self.assertEqual(invitation.status_code,200,invitation.text)
        token=invitation.json()['path'].split('/')[-1]
        password='TenantAccess!2026'
        activated=self.client.post(f'/api/invitations/{token}',json={'password':password})
        self.assertEqual(activated.status_code,200,activated.text)
        signed_in=self.client.post('/api/login',headers={'X-Haven-Portal':'tenant'},json={'email':activated.json()['email'],'password':password})
        self.assertEqual(signed_in.status_code,200,signed_in.text)
        submitted=self.client.post('/api/tenant/requests',headers={'X-Haven-Portal':'tenant','X-CSRF-Token':signed_in.json()['csrf']},json={'contract_id':contract['id'],'kind':'General request','subject':'Please contact me','body':'I need help with my tenancy.'})
        self.assertEqual(submitted.status_code,200,submitted.text)
        landlord_data=self.client.get('/api/data?company_id=1').json()
        self.assertTrue(any(r['id']==submitted.json()['id'] for r in landlord_data['requests']))
        self.assertTrue(any(n['target']=='portal-inbox' and n['body']=='Please contact me' for n in landlord_data['notifications']))

    def test_ticket_thread_reply_and_resolve_stay_with_the_request(self):
        contract=self.data['contracts'][0]
        token=self.post('/api/accounts/invite',{'kind':'tenant','target_id':contract['tenant_id']}).json()['path'].split('/')[-1]
        activated=self.client.post(f'/api/invitations/{token}',json={'password':'TenantAccess!2026'}).json()
        staff_client=self.client
        tenant=TestClient(app)
        signed=tenant.post('/api/login',headers={'X-Haven-Portal':'tenant'},json={'email':activated['email'],'password':'TenantAccess!2026'}).json()
        headers={'X-Haven-Portal':'tenant','X-CSRF-Token':signed['csrf']}
        rid=tenant.post('/api/tenant/requests',headers=headers,json={'contract_id':contract['id'],'kind':'Complaint','subject':'Noise','body':'Loud music at night.'}).json()['id']
        self.assertEqual(self.post(f'/api/requests/{rid}/respond',{'response':'We will speak to them.','status':'in progress'}).status_code,200)
        answer=[m for m in staff_client.get('/api/data?company_id=1').json()['portal_messages'] if m['subject']=='Response: Noise'][0]
        # the tenant's reply to the answer is a ticket notification, not a personal message
        sent=tenant.post('/api/tenant/messages',headers=headers,data={'contract_id':contract['id'],'subject':'Noise','body':'Thank you','thread_id':answer['id']})
        self.assertEqual(sent.status_code,200,sent.text)
        notes=staff_client.get('/api/data?company_id=1').json()['notifications']
        self.assertTrue(any(n['target']=='portal-inbox' and n['body']=='Response: Noise' for n in notes))
        self.assertFalse(any(n['target']=='tenant-messages' and n['body']=='Response: Noise' for n in notes))
        resolved=self.post(f'/api/requests/{rid}/status',{'status':'resolved'})
        self.assertEqual(resolved.status_code,200,resolved.text)
        self.assertEqual([r['status'] for r in staff_client.get('/api/data?company_id=1').json()['requests'] if r['id']==rid],['resolved'])
        self.assertEqual(self.post(f'/api/requests/{rid}/status',{'status':'bogus'}).status_code,422)
        tenant.close()

    def test_empty_invoice_run_explains_why(self):
        result=self.post('/api/invoices/run',{'company_id':1,'period':'2000-01'})
        self.assertEqual(result.status_code,200,result.text)
        body=result.json()
        self.assertEqual(body['created'],0)
        self.assertIn('2000-01',body['why'])

    def test_invoice_can_be_revised_with_a_trail_and_a_logo_appears_on_the_pdf(self):
        import json
        inv=self.data['invoices'][0]
        before=inv['total_cents']
        detail=self.client.get(f"/api/invoices/{inv['id']}/detail").json()
        self.assertTrue(detail['editable'])
        edit={'basic_rent':inv['basic_cents']/100,'service_charge':inv['service_cents']/100,'due_date':inv['due_date'],'note':'Thanks for your patience.',
              'items':[{'description':'Parking space','amount':50},{'description':'Goodwill discount','amount':-20}],'reason':'Add parking, small discount'}
        done=self.post(f"/api/invoices/{inv['id']}/edit",edit)
        self.assertEqual(done.status_code,200,done.text)
        self.assertEqual(done.json()['total_cents'],before+3000)
        after=self.client.get(f"/api/invoices/{inv['id']}/detail").json()
        self.assertEqual(after['revisions'][0]['reason'],'Add parking, small discount')
        self.assertEqual(after['balance_cents'],detail['balance_cents']+3000)
        self.assertEqual(self.post(f"/api/invoices/{inv['id']}/edit",edit).status_code,422)  # nothing changed
        # document fields can be edited in place and are kept in the trail
        edit['fields']={'reference':'Case 42','object':'Garden House 1, upstairs','invoice_date':'2026-10-01','address':'New street 1, 1011 AB Amsterdam'}
        edit['reason']='Details corrected'
        fielded=self.post(f"/api/invoices/{inv['id']}/edit",edit)
        self.assertEqual(fielded.status_code,200,fielded.text)
        shown=self.client.get(f"/api/invoices/{inv['id']}/detail").json()
        self.assertEqual(shown['fields']['reference'],'Case 42')
        self.assertEqual(shown['fields']['invoice_date'],'2026-10-01')
        self.assertEqual(shown['revisions'][0]['after']['fields']['reference'],'Case 42')
        edit['fields']={'invoice_date':'not-a-date'}; edit['reason']='bad date'
        self.assertEqual(self.post(f"/api/invoices/{inv['id']}/edit",edit).status_code,422)
        edit['fields']={'company_name':'   '}
        self.assertEqual(self.post(f"/api/invoices/{inv['id']}/edit",edit).status_code,422)
        edit['fields']={}
        edit['reason']='x'; edit['items']=[{'description':'Refund','amount':-100000}]
        self.assertEqual(self.post(f"/api/invoices/{inv['id']}/edit",edit).status_code,422)  # total must stay positive
        # the number, period and tenancy can never change, even by a direct database write
        with db.connect() as conn:
            with self.assertRaises(Exception):
                conn.execute("UPDATE invoices SET number='HACK' WHERE id=?",(inv['id'],))
        # logo: only PNG/JPEG, then it is embedded in the PDF
        png=bytes.fromhex('89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c6360f8cfc0f01f0005000201a5f645400000000049454e44ae426082')
        self.assertEqual(self.client.post('/api/companies/1/logo',files={'file':('x.txt',b'hello','text/plain')}).status_code,422)
        self.assertEqual(self.client.post('/api/companies/1/logo',files={'file':('logo.png',png,'image/png')}).status_code,200)
        self.assertEqual(self.client.get('/api/companies/1/logo').headers['content-type'],'image/png')
        pdf=self.client.get(f"/api/invoices/{inv['id']}/pdf")
        self.assertTrue(pdf.content.startswith(b'%PDF-'))
        self.assertIn(b'/Image',pdf.content)
        # once money is booked against the invoice, edits are refused
        with db.connect() as conn:
            row=dict(conn.execute('SELECT * FROM invoices WHERE id=?',(inv['id'],)).fetchone())
            conn.execute("INSERT INTO ledger(company_id,contract_id,invoice_id,kind,amount_cents,created_at,note) VALUES(?,?,?,?,?,?,?)",(row['company_id'],row['contract_id'],row['id'],'payment',-100,'2026-01-01T00:00:00+00:00','test'))
            conn.commit()
        edit['items']=[]; edit['reason']='again'
        self.assertEqual(self.post(f"/api/invoices/{inv['id']}/edit",edit).status_code,409)

    def test_invoice_list_shows_email_status_and_can_queue_an_email(self):
        listed=self.client.get('/api/invoices?company_id=1&page_size=50').json()
        self.assertIn('email_configured',listed)
        first=listed['items'][0]
        self.assertIn('email_status',first)
        self.assertNotIn('tenant_email',first)
        if not listed['email_configured']:
            self.assertTrue(first['email_note'])
            self.assertEqual(self.post(f"/api/invoices/{first['id']}/email",{}).status_code,409)
        with patch.dict(os.environ,{'HAVEN_SMTP_HOST':'smtp.invalid','HAVEN_SMTP_FROM':'billing@haven.test','HAVEN_PUBLIC_URL':'https://haven.test'}):
            with patch('app.email_delivery.deliver_pending_emails',lambda **k:{'sent':0,'failed':0,'queued':1}):
                queued=self.post(f"/api/invoices/{first['id']}/email",{})
            self.assertIn(queued.status_code,(200,422),queued.text)  # 422 when the demo tenant has no valid email
            if queued.status_code==200:
                again=self.client.get('/api/invoices?company_id=1&email=Queued').json()
                self.assertEqual([i['id'] for i in again['items']],[first['id']])

    def test_review_mode_keeps_new_invoices_as_drafts_until_they_are_sent(self):
        self.assertEqual(self.post('/api/companies/1/invoice-review',{'enabled':True}).status_code,200)
        tenant=TestClient(app)
        login=tenant.post('/api/login',headers={'X-Haven-Portal':'tenant'},json={'email':'tenant@haven.local','password':'HavenDemo!2026'})
        th={'X-Haven-Portal':'tenant','X-CSRF-Token':login.json()['csrf']}
        before=len(tenant.get('/api/tenant/data',headers=th).json()['invoices'])
        run=self.post('/api/invoices/run',{'company_id':1,'period':'2031-01'})
        self.assertEqual(run.status_code,200,run.text)
        made=run.json()['created']
        self.assertGreater(made,0)
        listed=self.client.get('/api/invoices?company_id=1&period=2031-01&page_size=100').json()['items']
        self.assertTrue(all(i['released_at'] is None and 'Draft' in i['email_note'] for i in listed))
        self.assertEqual(len(self.client.get('/api/invoices?company_id=1&email=Drafts&page_size=100').json()['items']),made)
        self.assertEqual(len(tenant.get('/api/tenant/data',headers=th).json()['invoices']),before)       # the tenant sees no draft
        draft=listed[0]
        self.assertEqual(tenant.get(f"/api/tenant/invoices/{draft['id']}/pdf",headers=th).status_code,404)
        edited=self.post(f"/api/invoices/{draft['id']}/edit",{'basic_rent':draft['basic_cents']/100+10,'service_charge':draft['service_cents']/100,'due_date':draft['due_date'],'reason':'Draft correction','items':[]})
        self.assertEqual(edited.status_code,200,edited.text)
        sent=self.post(f"/api/invoices/{draft['id']}/release",{})
        self.assertEqual(sent.status_code,200,sent.text)
        self.assertEqual(self.post(f"/api/invoices/{draft['id']}/release",{}).status_code,409)
        self.assertGreaterEqual(len(tenant.get('/api/tenant/data',headers=th).json()['invoices']),before)
        rest=self.post('/api/invoices/release',{'company_id':1})
        self.assertEqual(rest.json()['released'],made-1)
        self.assertEqual(len(self.client.get('/api/invoices?company_id=1&email=Drafts&page_size=100').json()['items']),0)
        tenant.close()

    def test_removing_a_tenant_frees_the_email_so_the_person_can_be_invited_again(self):
        # a fresh tenant with a portal login, then removed
        created=self.post('/api/records/tenants',{'company_id':1,'name':'Fleur Bakker','email':'fleur@example.com','phone':''})
        self.assertEqual(created.status_code,200,created.text)
        new_id=created.json()['id']
        token=self.post('/api/accounts/invite',{'kind':'tenant','target_id':new_id}).json()['path'].split('/')[-1]
        self.assertEqual(self.client.post(f'/api/invitations/{token}',json={'password':'TenantAccess!2026'}).status_code,200)
        self.assertEqual(self.post('/api/accounts/invite',{'kind':'tenant','target_id':new_id}).status_code,409)   # the email is taken while the login exists
        self.assertEqual(self.post(f'/api/tenants/{new_id}/archive',{'company_id':1}).status_code,200)
        again=self.post('/api/records/tenants',{'company_id':1,'name':'Fleur Bakker','email':'fleur@example.com','phone':''})
        self.assertEqual(again.status_code,200,again.text)
        invite=self.post('/api/accounts/invite',{'kind':'tenant','target_id':again.json()['id']})
        self.assertEqual(invite.status_code,200,invite.text)

    def test_payment_schedule_gives_the_due_payment_and_the_next_scheduled_payment(self):
        schedules={x['contract_id']:x for x in self.data['payment_schedules']}
        self.assertTrue(schedules)
        open_one=next(x for x in schedules.values() if x['due'])
        self.assertGreater(open_one['due']['amount_cents'],0)
        self.assertEqual(open_one['due']['overdue'],open_one['due']['days']<0)
        upcoming=next(x for x in schedules.values() if x['next'])
        self.assertGreaterEqual(upcoming['next']['days'],0)
        self.assertGreater(upcoming['next']['amount_cents'],0)
        self.assertEqual(len(upcoming['next']['date']),10)
        tenant=TestClient(app)
        login=tenant.post('/api/login',headers={'X-Haven-Portal':'tenant'},json={'email':'tenant@haven.local','password':'HavenDemo!2026'})
        mine=tenant.get('/api/tenant/data',headers={'X-Haven-Portal':'tenant','X-CSRF-Token':login.json()['csrf']}).json()['payment_schedules']
        self.assertTrue(mine and 'next' in mine[0] and 'due' in mine[0])
        tenant.close()

    def test_rent_invoice_download_is_a_formatted_pdf(self):
        import json
        # The bulk payload no longer carries the heavy snapshot; the PDF endpoint reads it from the database.
        listed=self.data['invoices'][0]
        self.assertNotIn('snapshot',listed)
        with db.connect() as conn:
            invoice=dict(conn.execute('SELECT * FROM invoices WHERE id=?',(listed['id'],)).fetchone())
        pdf=invoice_pdf_bytes(invoice,json.loads(invoice['snapshot']))
        self.assertTrue(pdf.startswith(b'%PDF-'))
        self.assertGreater(len(pdf),1500)

    def test_building_cost_allocations_are_snapshotted_per_tenant(self):
        occupied={c['property_id'] for c in self.data['contracts']}
        units=[p for p in self.data['properties'] if p['id'] in occupied][:2]
        group=self.post('/api/service-charge-groups',{'company_id':1,'name':'Test building','property_ids':[p['id'] for p in units],'shares':{str(units[0]['id']):25,str(units[1]['id']):75}})
        self.assertEqual(group.status_code,200,group.text)
        document_id=self.data['documents'][0]['id']
        cost_ids=[]
        for method in ('equal','floor_area','fixed_percentage'):
            response=self.post('/api/records/cost_lines',{'company_id':1,'service_group_id':group.json()['id'],'allocation_method':method,'year':2025,'category':method,'supplier':'Test supplier','amount':'120.00','document_id':document_id})
            self.assertEqual(response.status_code,200,response.text)
            cost_ids.append(response.json()['id'])
        self.login('finance')
        settlement=self.post('/api/settlements',{'service_group_id':group.json()['id'],'year':2025})
        self.assertEqual(settlement.status_code,200,settlement.text)
        with db.connect() as conn:
            snapshot=__import__('json').loads(conn.execute('SELECT snapshot FROM settlements WHERE id=?',(settlement.json()['id'],)).fetchone()['snapshot'])
        self.assertEqual(len(snapshot['lines']),2)
        allocations={line['property_id']:{item['allocation_method']:item for item in line['costs']} for line in snapshot['lines']}
        first,second=units
        self.assertAlmostEqual(allocations[first['id']]['equal']['unit_weight'],0.5)
        self.assertAlmostEqual(allocations[first['id']]['floor_area']['unit_weight'],first['area']/(first['area']+second['area']))
        self.assertAlmostEqual(allocations[first['id']]['fixed_percentage']['unit_weight'],0.25)
        self.assertEqual(sum(item['allocated_cents'] for line in snapshot['lines'] for item in line['costs'] if item['allocation_method']=='equal'),12000)
        self.assertEqual(sum(item['allocated_cents'] for line in snapshot['lines'] for item in line['costs'] if item['allocation_method']=='fixed_percentage'),12000)
        self.login('admin')
        approved=self.post(f'/api/settlements/{settlement.json()["id"]}/approve',{'receipt_confirmed':True,'note':'Allocation checked'})
        self.assertEqual(approved.status_code,200,approved.text)
        self.login('manager')
        published=self.post(f'/api/settlements/{settlement.json()["id"]}/publish',{'confirm_individual_statements':True})
        self.assertEqual(published.status_code,200,published.text)
        self.assertEqual(published.json()['published'],2)
        self.login('admin')
        unit_cost=self.post('/api/records/cost_lines',{'company_id':1,'property_id':second['id'],'entry_method':'manual','year':2025,'category':'Unit repair','supplier':'Local supplier','amount':'20.00'})
        self.assertEqual(unit_cost.status_code,200,unit_cost.text)
        own_draft=self.post('/api/settlements',{'property_id':second['id'],'year':2025})
        self.assertEqual(own_draft.status_code,200,own_draft.text)
        self.assertEqual(self.post(f'/api/settlements/{own_draft.json()["id"]}/approve',{'receipt_confirmed':True,'note':'Reviewed by administrator'}).status_code,200)

    def test_service_charge_costs_allow_manual_entry_and_csv_import(self):
        prop=self.data['properties'][0]
        manual=self.post('/api/records/cost_lines',{'company_id':1,'property_id':prop['id'],'entry_method':'manual','year':2025,'category':'Cleaning','supplier':'Local supplier','amount':'32.50','document_id':''})
        self.assertEqual(manual.status_code,200,manual.text)
        with db.connect() as conn:
            saved=conn.execute('SELECT document_id,entry_method FROM cost_lines WHERE id=?',(manual.json()['id'],)).fetchone()
        self.assertIsNone(saved['document_id'])
        self.assertEqual(saved['entry_method'],'manual')
        csv_data=f'scope_type,scope_name,year,category,supplier,amount,allocation_method,document_name\nunit,"{prop["name"]}",2025,Water,Utility Co,48.00,tenancy_days,\n'.encode('utf-8')
        imported=self.client.post('/api/service-charge-costs/import',data={'company_id':'1'},files={'file':('costs.csv',csv_data,'text/csv')})
        self.assertEqual(imported.status_code,200,imported.text)
        self.assertEqual(imported.json()['imported'],1)
        refreshed=self.client.get('/api/data?company_id=1').json()
        self.assertEqual(len(refreshed['invoices']),5)

    def test_new_invoices_are_emailed_with_pdf_when_smtp_is_configured(self):
        smtp_env={
            'HAVEN_SMTP_HOST':'smtp.example.test','HAVEN_SMTP_PORT':'587',
            'HAVEN_SMTP_FROM':'haven@example.test','HAVEN_PUBLIC_URL':'https://haven.example.test',
        }
        with patch.dict(os.environ,smtp_env), patch('app.email_delivery.smtplib.SMTP') as smtp:
            server=smtp.return_value
            server.__enter__.return_value=server
            response=self.post('/api/invoices/run',{'company_id':1,'period':'2027-02'})
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(response.json()['created'],5)
            with db.connect() as conn:
                delivery_jobs=[dict(row) for row in conn.execute('SELECT kind,status,last_error FROM outbound_email_jobs')]
            self.assertEqual(response.json()['email_delivery']['sent'],5,(response.json(),delivery_jobs))
            self.assertEqual(server.send_message.call_count,5)
            for call in server.send_message.call_args_list:
                message=call.args[0]
                attachments=list(message.iter_attachments())
                self.assertEqual(len(attachments),1)
                self.assertEqual(attachments[0].get_content_type(),'application/pdf')
                self.assertTrue(attachments[0].get_payload(decode=True).startswith(b'%PDF'))
        data=self.client.get('/api/data?company_id=1').json()
        self.assertEqual(sum(1 for job in data['outbound_emails'] if job['kind']=='invoice_issued' and job['status']=='sent'),5)

    def test_overdue_invoice_starts_arrears_case_once_and_gates_referral(self):
        invoice=self.data['invoices'][0]
        with db.transaction() as conn:
            # The test database is isolated; backdate this fixture without changing
            # the production rule that issued invoices are immutable.
            conn.execute('DROP TRIGGER invoice_no_update')
            conn.execute('UPDATE invoices SET due_date=? WHERE id=?',((date.today()-timedelta(days=31)).isoformat(),invoice['id']))
        first=self.post('/api/arrears/run',{'company_id':1})
        self.assertEqual(first.status_code,200,first.text)
        self.assertGreaterEqual(first.json()['created'],1)
        second=self.post('/api/arrears/run',{'company_id':1})
        self.assertEqual(second.status_code,200,second.text)
        self.assertEqual(second.json()['created'],0)
        data=self.client.get('/api/data?company_id=1').json()
        case=next(c for c in data['arrears_cases'] if c['invoice_id']==invoice['id'])
        self.assertEqual(case['status'],'open')
        self.assertEqual(case['balance_cents'],invoice['total_cents'])
        self.assertEqual(self.post(f"/api/arrears/cases/{case['id']}/step",{
            'step':'municipality_reported','occurred_on':date.today().isoformat(),
            'municipality_process_confirmed':True,'municipality_consent_confirmed':True,
        }).status_code,422)

    def test_overdue_courtesy_emails_queue_once_and_deliver_through_smtp(self):
        from app.email_delivery import deliver_pending_emails
        from app.main import process_overdue_invoices
        invoice=self.data['invoices'][0]
        followup_invoice=self.data['invoices'][1]
        with db.transaction() as conn:
            conn.execute('DROP TRIGGER invoice_no_update')
            conn.execute('UPDATE invoices SET due_date=?',(date.today().isoformat(),))
            conn.execute('UPDATE invoices SET due_date=? WHERE id=?',((date.today()-timedelta(days=9)).isoformat(),invoice['id']))
            conn.execute('UPDATE invoices SET due_date=? WHERE id=?',((date.today()-timedelta(days=3)).isoformat(),followup_invoice['id']))
        smtp_env={
            'HAVEN_SMTP_HOST':'smtp.example.test','HAVEN_SMTP_PORT':'587',
            'HAVEN_SMTP_FROM':'haven@example.test','HAVEN_PUBLIC_URL':'https://haven.example.test',
        }
        with patch.dict(os.environ,smtp_env):
            process_overdue_invoices(1)
            with db.connect() as conn:
                jobs=conn.execute('SELECT kind,status FROM outbound_email_jobs WHERE invoice_id=? ORDER BY kind',(invoice['id'],)).fetchall()
            self.assertEqual({r['kind'] for r in jobs},{'overdue_followup'})
            with db.connect() as conn:
                initial=conn.execute('SELECT kind,status FROM outbound_email_jobs WHERE invoice_id=?',(followup_invoice['id'],)).fetchall()
            self.assertEqual([(r['kind'],r['status']) for r in initial],[('overdue_friendly','queued')])
            self.assertTrue(all(r['status']=='queued' for r in jobs))
            process_overdue_invoices(1)
            with patch('app.email_delivery.smtplib.SMTP') as smtp:
                server=smtp.return_value
                server.__enter__.return_value=server
                result=deliver_pending_emails()
                with db.connect() as conn:
                    states=[dict(r) for r in conn.execute('SELECT status,last_error,next_attempt_at FROM outbound_email_jobs')]
                self.assertEqual(result['sent'],2,(result,states))
                self.assertEqual(server.send_message.call_count,2)
            with db.connect() as conn:
                count=conn.execute('SELECT count(*) FROM outbound_email_jobs').fetchone()[0]
                sent=conn.execute('SELECT count(*) FROM outbound_email_jobs WHERE status="sent"').fetchone()[0]
            self.assertEqual(count,2)
            self.assertEqual(sent,2)

    def test_tenant_and_staff_can_reply_in_one_private_portal_thread(self):
        self.login('manager')
        tenant_login=self.client.post('/api/login',headers={'X-Haven-Portal':'tenant'},json={'email':'tenant@haven.local','password':'HavenDemo!2026'})
        self.assertEqual(tenant_login.status_code,200,tenant_login.text)
        self.client.headers['X-Haven-Portal']='tenant'
        self.client.headers['X-CSRF-Token']=tenant_login.json()['csrf']
        tenant_contracts=self.client.get('/api/tenant/data').json()['contracts']
        contract_id=tenant_contracts[0]['id']
        self.client.headers.pop('X-Haven-Portal',None)
        self.login('manager')
        started=self.post('/api/portal/messages',{'contract_id':contract_id,'subject':'Repair appointment','body':'Can we arrange a visit next week?'})
        self.assertEqual(started.status_code,200,started.text)
        thread_id=started.json()['id']

        tenant_login=self.client.post('/api/login',headers={'X-Haven-Portal':'tenant'},json={'email':'tenant@haven.local','password':'HavenDemo!2026'})
        self.assertEqual(tenant_login.status_code,200,tenant_login.text)
        self.client.headers['X-Haven-Portal']='tenant'
        self.client.headers['X-CSRF-Token']=tenant_login.json()['csrf']
        tenant_reply=self.client.post('/api/tenant/messages',data={'contract_id':contract_id,'thread_id':thread_id,'subject':'ignored on reply','body':'Tuesday works for me.'})
        self.assertEqual(tenant_reply.status_code,200,tenant_reply.text)

        self.client.headers.pop('X-Haven-Portal',None)
        self.login('manager')
        staff_reply=self.post('/api/portal/messages',{'contract_id':contract_id,'thread_id':thread_id,'body':'Confirmed for Tuesday morning.'})
        self.assertEqual(staff_reply.status_code,200,staff_reply.text)

        tenant_login=self.client.post('/api/login',headers={'X-Haven-Portal':'tenant'},json={'email':'tenant@haven.local','password':'HavenDemo!2026'})
        self.assertEqual(tenant_login.status_code,200,tenant_login.text)
        self.client.headers['X-Haven-Portal']='tenant'
        self.client.headers['X-CSRF-Token']=tenant_login.json()['csrf']
        tenant_data=self.client.get('/api/tenant/data').json()
        thread=[m for m in tenant_data['messages'] if m['thread_id']==thread_id]
        self.assertEqual(len(thread),3)
        self.assertEqual(thread[0]['subject'],'Repair appointment')

    def test_receipt_does_not_pay_invoice_until_verified(self):
        inv=self.data['invoices'][0]
        token=self.link(inv['id'])
        before=self.client.get('/api/public/'+token).json()['balance_cents']
        response=self.submit(token)
        self.assertEqual(response.status_code,200,response.text)
        proof=response.json()['id']
        self.assertEqual(self.client.get('/api/public/'+token).json()['balance_cents'],before)
        r=self.post(f'/api/proofs/{proof}/review',{'decision':'approved','note':'Checked actual receipt','receipt_confirmed':False})
        self.assertEqual(r.status_code,422)
        r=self.post(f'/api/proofs/{proof}/review',{'decision':'approved','note':'Checked actual receipt','receipt_confirmed':True})
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(self.client.get('/api/public/'+token).json()['balance_cents'],before-10000)
        self.assertEqual(self.post(f'/api/proofs/{proof}/review',{'decision':'approved','note':'Retry','receipt_confirmed':True}).status_code,409)
        self.assertEqual(self.post(f'/api/proofs/{proof}/reverse',{'note':'Receipt was matched incorrectly'}).status_code,200)
        self.assertEqual(self.client.get('/api/public/'+token).json()['balance_cents'],before)
        self.assertEqual(self.post(f'/api/proofs/{proof}/reverse',{'note':'Retry'}).status_code,409)

    def test_duplicate_receipts_and_invalid_uploads(self):
        token=self.link(self.data['invoices'][0]['id'])
        self.assertEqual(self.submit(token).status_code,200)
        self.assertEqual(self.submit(token,reference='DIFFERENT').status_code,409)
        self.assertEqual(self.submit(token,content=b'%PDF-1.4 different').status_code,409)
        self.assertEqual(self.submit(token,reference='HTML',content=b'<html>not a PDF</html>').status_code,422)
        self.assertEqual(self.submit(token,amount='1.001',reference='ROUND',content=b'%PDF-1.4 third').status_code,422)

    def test_staff_cannot_approve_own_submission(self):
        self.login('finance')
        inv=self.data['invoices'][0]
        response=self.client.post(f'/api/invoices/{inv["id"]}/proof',data={'amount':'50','paid_on':date.today().isoformat(),'method':'Cash'},files={'file':('cash.pdf',b'%PDF-1.4 cash receipt','application/pdf')})
        self.assertEqual(response.status_code,200,response.text)
        proof=response.json()['id']
        review={'decision':'approved','note':'Cash independently counted','receipt_confirmed':True}
        self.assertEqual(self.post(f'/api/proofs/{proof}/review',review).status_code,403)
        self.login('manager')
        self.assertEqual(self.post(f'/api/proofs/{proof}/review',review).status_code,200)

    def test_links_expire_and_revoke(self):
        inv=self.data['invoices'][0]
        first=self.link(inv['id'])
        second=self.link(inv['id'])
        self.assertEqual(self.client.get('/api/public/'+first).status_code,404)
        self.assertEqual(self.client.get('/api/public/'+second).status_code,200)
        self.post(f'/api/invoices/{inv["id"]}/revoke-link',{})
        self.assertEqual(self.client.get('/api/public/'+second).status_code,404)
        third=self.link(inv['id'])
        with db.transaction() as conn:
            conn.execute('UPDATE upload_links SET expires_at=?',('2000-01-01',))
        self.assertEqual(self.client.get('/api/public/'+third).status_code,404)

    def test_roles_documents_and_immutable_ledger(self):
        response=self.post('/api/staff',{'company_id':1,'name':'Property Staff','email':'property@haven.local','password':'HavenDemo!2026','role':'property'})
        self.assertEqual(response.status_code,200,response.text)
        self.login('property')
        view=self.client.get('/api/data?company_id=1').json()
        self.assertNotIn('payment_proofs',view)
        doc=self.data['payment_proofs'][0]['document_id']
        self.assertEqual(self.client.get(f'/api/documents/{doc}/file').status_code,403)
        self.assertEqual(self.post('/api/invoices/run',{'company_id':1,'period':'2027-01'}).status_code,403)
        with db.connect() as conn:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute('UPDATE ledger SET amount_cents=0')
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute('DELETE FROM audit')

    def test_payment_review_rechecks_remaining_balance(self):
        inv=self.data['invoices'][0]
        token=self.link(inv['id'])
        balance=self.client.get('/api/public/'+token).json()['balance_cents']
        amount=str(balance/100)
        a=self.submit(token,amount,'FULL-A',b'%PDF-1.4 receipt A').json()['id']
        b=self.submit(token,amount,'FULL-B',b'%PDF-1.4 receipt B').json()['id']
        review={'decision':'approved','note':'Confirmed','receipt_confirmed':True}
        self.assertEqual(self.post(f'/api/proofs/{a}/review',review).status_code,200)
        self.assertEqual(self.post(f'/api/proofs/{b}/review',review).status_code,409)

    def test_deposit_requires_second_approver(self):
        c=self.data['contracts'][0]
        d=self.data['documents'][0]
        response=self.post('/api/deposits',{'contract_id':c['id'],'kind':'deduction','amount':'25','reason':'Documented damage','deduction_type':'Tenant damage','document_id':d['id']})
        self.assertEqual(response.status_code,200,response.text)
        event=response.json()['id']
        review={'decision':'approved','note':'Reviewed evidence'}
        self.assertEqual(self.post(f'/api/deposits/{event}/review',review).status_code,403)
        self.login('manager')
        self.assertEqual(self.post(f'/api/deposits/{event}/review',review).status_code,200)

    def test_repairs_require_completion_evidence(self):
        r=self.data['repairs'][0]
        self.assertEqual(self.post(f'/api/repairs/{r["id"]}/status',{'status':'done'}).status_code,422)
        private=next(d for d in self.data['documents'] if d['kind'] in ('payment proof','supplier invoice','deposit evidence'))
        self.assertEqual(self.post(f'/api/repairs/{r["id"]}/status',{'status':'done','document_id':private['id'],'note':'Must not leak finance paperwork'}).status_code,422)
        upload=self.client.post('/api/documents',data={'company_id':'1','kind':'Repair evidence'},files={'file':('fixed.pdf',b'%PDF-1.4\nFixed','application/pdf')})
        self.assertEqual(upload.status_code,200,upload.text)
        self.assertEqual(self.post(f'/api/repairs/{r["id"]}/status',{'status':'done','document_id':upload.json()['id'],'note':'Repair completed and documented'}).status_code,200)

    def test_pdf_and_audit_verification(self):
        inv=self.data['invoices'][0]
        response=self.client.get(f'/api/invoices/{inv["id"]}/pdf')
        self.assertEqual(response.status_code,200,response.text[:100])
        self.assertTrue(response.content.startswith(b'%PDF'))
        audit=self.client.get('/api/audit/verify').json()
        self.assertTrue(audit['valid'])
        self.assertGreater(audit['entries'],0)

    def test_rent_change_is_versioned_and_requires_second_person(self):
        self.login('manager')
        c=self.data['contracts'][0]
        effective=(date.today()+timedelta(days=100)).isoformat()
        result=self.post('/api/records/rent_proposals',{'company_id':1,'contract_id':c['id'],'effective_from':effective,'basic_rent':'1200','service_charge':'75','reason':'Agreed variation','source':'Independent test source'})
        self.assertEqual(result.status_code,200,result.text)
        pid=result.json()['id']
        review={'decision':'approved','note':'Independently verified','receipt_confirmed':True}
        self.assertEqual(self.post(f'/api/rent-proposals/{pid}/review',review).status_code,403)
        self.login('admin')
        self.assertEqual(self.post(f'/api/rent-proposals/{pid}/review',review).status_code,200)
        data=self.client.get('/api/data?company_id=1').json()
        versions=[r for r in data['rent_versions'] if r['contract_id']==c['id']]
        self.assertEqual(len(versions),2)
        self.assertEqual(max(versions,key=lambda r:r['effective_from'])['basic_cents'],120000)

    def test_only_rejected_rent_proposals_can_be_removed_and_removal_is_audited(self):
        c=self.data['contracts'][0]
        effective=(date.today()+timedelta(days=100)).isoformat()
        result=self.post('/api/records/rent_proposals',{'company_id':1,'contract_id':c['id'],'effective_from':effective,'basic_rent':'1200','service_charge':'75','reason':'Test proposal','source':'Test source'})
        self.assertEqual(result.status_code,200,result.text)
        pid=result.json()['id']
        self.assertEqual(self.client.delete(f'/api/rent-proposals/{pid}').status_code,409)
        self.login('manager')
        rejected=self.post(f'/api/rent-proposals/{pid}/review',{'decision':'rejected','note':'Needs correction'})
        self.assertEqual(rejected.status_code,200,rejected.text)
        self.login('admin')
        removed=self.client.delete(f'/api/rent-proposals/{pid}')
        self.assertEqual(removed.status_code,200,removed.text)
        proposals=self.client.get('/api/data?company_id=1').json()['rent_proposals']
        self.assertFalse(any(p['id']==pid for p in proposals))
        audit=self.client.get('/api/audit/verify').json()
        self.assertTrue(audit['valid'])

    def test_portfolio_csv_preview_then_atomic_import(self):
        content=(
            'property_name,address,city,postcode,kind,area,rooms,energy_label,bag_id,tenant_name,tenant_email,tenant_phone,tenant_language,signed_on,start_date,end_date,basic_rent,service_charge,deposit\n'
            'Import House,10 Example Street,Amsterdam,1011AA,House,82,3,C,,Test Tenant,new.tenant@example.com,+31201234567,nl,2025-01-01,2025-02-01,,1250.00,75.00,2500.00\n'
            ',,,,,,,,,Contact Only,contact@example.com,,,,,,,,\n'
        ).encode()
        before=self.client.get('/api/data?company_id=1').json()
        preview=self.client.post('/api/portfolio/import/preview',data={'company_id':'1'},files={'file':('portfolio.csv',content,'text/csv')})
        self.assertEqual(preview.status_code,200,preview.text)
        self.assertEqual(preview.json(),{'properties_created':1,'tenants_created':2,'contracts_created':1,'rows':2})
        after_preview=self.client.get('/api/data?company_id=1').json()
        self.assertEqual(len(after_preview['properties']),len(before['properties']))
        self.assertEqual(len(after_preview['tenants']),len(before['tenants']))
        imported=self.client.post('/api/portfolio/import',data={'company_id':'1'},files={'file':('portfolio.csv',content,'text/csv')})
        self.assertEqual(imported.status_code,200,imported.text)
        self.assertEqual(imported.json()['contracts_created'],1)
        self.assertEqual(imported.json()['properties_created'],1)
        self.assertEqual(imported.json()['tenants_created'],2)
        records=self.client.get('/api/data?company_id=1').json()
        self.assertTrue(any(p['name']=='Import House' for p in records['properties']))
        self.assertTrue(any(t['email']=='contact@example.com' for t in records['tenants']))

    def test_portfolio_import_rolls_back_every_row_on_validation_error(self):
        content=(
            'property_name,address,city,tenant_name,tenant_email,signed_on,start_date,basic_rent\n'
            'Must Roll Back,1 Example Street,Amsterdam,,,,,\n'
            ',,,Invalid Tenant,bad-email,,,,\n'
        ).encode()
        response=self.client.post('/api/portfolio/import',data={'company_id':'1'},files={'file':('bad.csv',content,'text/csv')})
        self.assertEqual(response.status_code,422,response.text)
        self.assertIn('CSV row 3',response.json()['detail'])
        records=self.client.get('/api/data?company_id=1').json()
        self.assertFalse(any(p['name']=='Must Roll Back' for p in records['properties']))

    def test_percentage_indexation_calculation_and_admin_self_review(self):
        c=self.data['contracts'][0]
        current=max((r for r in self.data['rent_versions'] if r['contract_id']==c['id']),key=lambda r:r['effective_from'])
        effective=(date.today()+timedelta(days=400)).isoformat()
        result=self.post('/api/rent-indexations/propose',{'company_id':1,'contract_id':c['id'],'effective_from':effective,'percentage':'4.25','note':'Contract clause 7'})
        self.assertEqual(result.status_code,200,result.text)
        # The endpoint rounds the calculated result to the nearest cent.
        expected=int((__import__('decimal').Decimal(current['basic_cents'])*__import__('decimal').Decimal('1.0425')).quantize(__import__('decimal').Decimal('1'),rounding=__import__('decimal').ROUND_HALF_UP))
        self.assertEqual(result.json()['basic_cents'],expected)
        self.assertEqual(result.json()['service_cents'],current['service_cents'])
        pid=result.json()['id']
        self.assertEqual(self.post(f'/api/rent-proposals/{pid}/review',{'decision':'approved','note':'Checked','receipt_confirmed':False}).status_code,422)
        self.assertEqual(self.post(f'/api/rent-proposals/{pid}/review',{'decision':'approved','note':'Checked legal and contract basis','receipt_confirmed':True}).status_code,200)

    def test_cbs_cpi_lookup_and_proposal_store_the_source(self):
        from unittest.mock import patch
        c=self.data['contracts'][0]
        cpi={'table':'86141ENG','period':'2025-08','index':101.06,'percentage':2.8,'source_url':'https://opendata.cbs.nl/statline/#/CBS/en/dataset/86141ENG/table'}
        with patch('app.main.cpi_annual_rate',return_value=cpi):
            lookup=self.client.get('/api/rent-indexations/cbs',params={'company_id':1,'period':'2025-08'})
            self.assertEqual(lookup.status_code,200,lookup.text)
            self.assertEqual(lookup.json()['percentage'],2.8)
            effective=(date.today()+timedelta(days=400)).isoformat()
            result=self.post('/api/rent-indexations/propose',{'company_id':1,'contract_id':c['id'],'effective_from':effective,'cpi_period':'2025-08','percentage':'','note':'Contract clause 7'})
        self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(result.json()['percentage'],'2.8')
        with db.connect() as conn:
            saved=conn.execute('SELECT reason,source FROM rent_proposals WHERE id=?',(result.json()['id'],)).fetchone()
        self.assertIn('CBS CPI year-on-year for 2025-08',saved['reason'])
        self.assertIn('86141ENG',saved['source'])


    def test_manually_signed_contract_requires_confirmation_and_shares_pdf(self):
        occupied={c['property_id'] for c in self.data['contracts']}
        prop=next(p for p in self.data['properties'] if p['id'] not in occupied)
        with db.connect() as conn:
            tenant_id=conn.execute('SELECT tenant_id FROM portal_accounts WHERE company_id=1 AND enabled=1 ORDER BY tenant_id LIMIT 1').fetchone()['tenant_id']
        tenant=next(t for t in self.data['tenants'] if t['id']==tenant_id)
        start=(date.today()+timedelta(days=400)).isoformat()
        upload=self.client.post('/api/documents',data={'company_id':'1','kind':'Contract'},files={'file':('signed-agreement.pdf',b'%PDF-1.4\nSigned agreement test','application/pdf')})
        self.assertEqual(upload.status_code,200,upload.text)
        payload={'company_id':1,'property_id':prop['id'],'tenant_id':tenant['id'],'signed_on':date.today().isoformat(),'start_date':start,'basic_rent':'900','service_charge':'50','deposit':'1000','document_id':upload.json()['id']}
        missing_confirmation=self.post('/api/contracts/manual',payload)
        self.assertEqual(missing_confirmation.status_code,422)
        self.assertIn('both parties',missing_confirmation.json()['detail'])
        payload['confirm_signed']=True
        response=self.post('/api/contracts/manual',payload)
        self.assertEqual(response.status_code,200,response.text)
        contract_id=response.json()['id']
        with db.connect() as conn:
            contract=conn.execute('SELECT status,signed_on FROM contracts WHERE id=?',(contract_id,)).fetchone()
            self.assertEqual((contract['status'],contract['signed_on']),('active',date.today().isoformat()))
            self.assertIsNotNone(conn.execute('SELECT id FROM shared_documents WHERE contract_id=? AND document_id=?',(contract_id,upload.json()['id'])).fetchone())
            self.assertTrue(conn.execute('SELECT id FROM notifications WHERE title=? AND target="documents"',( 'Signed rental agreement available',)).fetchone())
        self.assertEqual(self.client.get('/api/contracts/1/signatures').status_code,404)

    def test_signed_agreement_attaches_to_matching_existing_tenancy(self):
        contract=self.data['contracts'][0]
        before=len(self.data['contracts'])
        upload=self.client.post('/api/documents',data={'company_id':'1','kind':'Contract'},files={'file':('signed-renewal.pdf',b'%PDF-1.4\nSigned renewal','application/pdf')})
        self.assertEqual(upload.status_code,200,upload.text)
        result=self.post('/api/contracts/manual',{'company_id':1,'property_id':contract['property_id'],'tenant_id':contract['tenant_id'],'signed_on':date.today().isoformat(),'start_date':date.today().isoformat(),'basic_rent':'9999','service_charge':'999','deposit':'999','document_id':upload.json()['id'],'confirm_signed':True})
        self.assertEqual(result.status_code,200,result.text)
        self.assertTrue(result.json()['existing'])
        self.assertEqual(result.json()['id'],contract['id'])
        with db.connect() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM contracts').fetchone()[0],before)
            self.assertIsNotNone(conn.execute('SELECT id FROM shared_documents WHERE contract_id=? AND document_id=?',(contract['id'],upload.json()['id'])).fetchone())

    def test_document_removal_revokes_sharing_and_protects_evidence(self):
        upload=self.client.post('/api/documents',data={'company_id':'1','kind':'General'},files={'file':('temporary.pdf',b'%PDF-1.4\nTemporary file','application/pdf')})
        self.assertEqual(upload.status_code,200,upload.text)
        document_id=upload.json()['id']
        with db.connect() as conn:
            doc=conn.execute('SELECT file_key FROM documents WHERE id=?',(document_id,)).fetchone()
            path=db.DATA_DIR/'uploads'/doc['file_key']
            contract=self.data['contracts'][0]
            user_id=conn.execute('SELECT id FROM users WHERE email="admin@haven.local"').fetchone()['id']
        with db.transaction() as conn:
            conn.execute('INSERT INTO shared_documents(company_id,contract_id,document_id,shared_by,shared_at) VALUES(?,?,?,?,?)',(1,contract['id'],document_id,user_id,'2026-10-13T00:00:00Z'))
        removed=self.client.delete(f'/api/documents/{document_id}')
        self.assertEqual(removed.status_code,200,removed.text)
        self.assertFalse(path.exists())
        with db.connect() as conn:
            self.assertIsNone(conn.execute('SELECT id FROM shared_documents WHERE document_id=?',(document_id,)).fetchone())
        protected=self.client.delete(f"/api/documents/{self.data['documents'][0]['id']}")
        self.assertEqual(protected.status_code,409,protected.text)

    def test_tenant_document_access_can_be_revoked_without_deleting_file(self):
        upload=self.client.post('/api/documents',data={'company_id':'1','kind':'General'},files={'file':('tenant-guide.pdf',b'%PDF-1.4\nGuide','application/pdf')})
        self.assertEqual(upload.status_code,200,upload.text)
        doc_id=upload.json()['id'];contract=self.data['contracts'][0]
        shared=self.post(f'/api/documents/{doc_id}/share',{'contract_id':contract['id']})
        self.assertEqual(shared.status_code,200,shared.text)
        doc=next(d for d in self.client.get('/api/data?company_id=1').json()['documents'] if d['id']==doc_id)
        self.assertEqual(doc['shared_count'],1)
        revoked=self.post(f'/api/documents/{doc_id}/revoke-sharing',{})
        self.assertEqual(revoked.status_code,200,revoked.text)
        self.assertEqual(revoked.json()['revoked'],1)
        doc=next(d for d in self.client.get('/api/data?company_id=1').json()['documents'] if d['id']==doc_id)
        self.assertEqual(doc['shared_count'],0)
        self.assertEqual(self.client.get(f'/api/documents/{doc_id}/file').status_code,200)



if __name__ == '__main__':
    unittest.main()



def _owner_statement_flow(self):
    today = date.today()
    owners = self.client.get('/api/data?company_id=1').json()
    self.assertEqual(len(owners['owners']), 1)
    portfolio = owners['portfolios'][0]
    self.assertTrue(all(p['portfolio_id'] == portfolio['id'] for p in owners['properties']))
    # Move this month's ledger activity into last month so the statement has something to report.
    last = (today.replace(day=1) - timedelta(days=1))
    period = last.strftime('%Y-%m')
    with db.transaction() as conn:
        conn.execute('DROP TRIGGER ledger_no_update')
        conn.execute("UPDATE ledger SET created_at=? WHERE kind='payment'", (last.isoformat() + 'T12:00:00+00:00',))
    collected = -sum(l['amount_cents'] for l in owners['ledger'] if l['kind'] == 'payment')
    self.assertGreater(collected, 0)
    bad = self.post('/api/owner-statements/generate', {'company_id': 1, 'portfolio_id': portfolio['id'], 'period': today.strftime('%Y-%m')})
    self.assertEqual(bad.status_code, 422)
    self.login('finance')
    expense = self.post('/api/owner-expenses', {'company_id': 1, 'portfolio_id': portfolio['id'], 'property_id': owners['properties'][0]['id'], 'incurred_on': last.isoformat(), 'category': 'Insurance', 'description': 'Building insurance', 'amount': '100.00'})
    self.assertEqual(expense.status_code, 200, expense.text)
    gen = self.post('/api/owner-statements/generate', {'company_id': 1, 'portfolio_id': portfolio['id'], 'period': period})
    self.assertEqual(gen.status_code, 200, gen.text)
    sid = gen.json()['id']
    fee = round(collected * 0.06)
    self.assertEqual(gen.json()['net_cents'], collected - fee - 10000)
    self.assertEqual(self.post('/api/owner-statements/generate', {'company_id': 1, 'portfolio_id': portfolio['id'], 'period': period}).status_code, 409)
    self.assertEqual(self.client.get(f'/api/owner-statements/{sid}/pdf').headers['content-type'], 'application/pdf')
    self.assertEqual(self.post(f'/api/owner-statements/{sid}/publish', {'confirmed': True}).status_code, 403)
    self.login('manager')
    self.assertEqual(self.post(f'/api/owner-statements/{sid}/publish', {}).status_code, 422)
    self.assertEqual(self.post(f'/api/owner-statements/{sid}/publish', {'confirmed': True}).status_code, 200)
    with self.assertRaises(sqlite3.IntegrityError):
        with db.transaction() as conn:
            conn.execute('UPDATE owner_statements SET net_cents=1 WHERE id=?', (sid,))
    self.assertEqual(self.post(f'/api/owner-statements/{sid}/paid', {'paid_on': today.isoformat(), 'reference': 'MANUAL-1'}).status_code, 422)
    self.assertEqual(self.post(f'/api/owner-statements/{sid}/paid', {'paid_on': today.isoformat(), 'reference': 'MANUAL-1', 'confirmed': True}).status_code, 200)
    # Owner portal: separate session, own data only.
    owner = TestClient(app)
    r = owner.post('/api/login', headers={'X-Haven-Portal': 'owner'}, json={'email': 'owner@haven.local', 'password': 'HavenDemo!2026'})
    self.assertEqual(r.status_code, 200, r.text)
    headers = {'X-Haven-Portal': 'owner', 'X-CSRF-Token': r.json()['csrf']}
    data = owner.get('/api/owner/data', headers=headers).json()
    self.assertEqual([s['id'] for s in data['statements']], [sid])
    self.assertEqual(data['statements'][0]['status'], 'paid')
    self.assertEqual(len(data['units']), 6)
    self.assertEqual(owner.get(f'/api/owner/statements/{sid}/pdf', headers=headers).status_code, 200)
    self.assertEqual(owner.get('/api/data?company_id=1', headers=headers).status_code, 403)
    self.assertEqual(owner.get('/api/tenant/data', headers={'X-Haven-Portal': 'tenant'}).status_code, 401)
    owner.close()


WorkflowTests.test_owner_statement_lifecycle_and_portal = _owner_statement_flow
