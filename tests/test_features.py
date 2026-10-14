import contextlib
import io
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from app import db
from app.main import app
from manage import demo

PDF = b'%PDF-1.4\nfeature test document'


class FeatureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original_dir = db.DATA_DIR

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        db.DATA_DIR = Path(self.temp.name)
        with contextlib.redirect_stdout(io.StringIO()):
            demo()
        self.client = TestClient(app)
        self.login('manager')

    def tearDown(self):
        self.client.close()
        db.DATA_DIR = self.original_dir
        self.temp.cleanup()

    def login(self, role):
        r = self.client.post('/api/login', json={'email': f'{role}@haven.local', 'password': 'HavenDemo!2026'})
        self.assertEqual(r.status_code, 200, r.text)
        self.client.headers['X-CSRF-Token'] = r.json()['csrf']

    def data(self):
        return self.client.get('/api/data?company_id=1').json()

    def post(self, path, body):
        return self.client.post(path, json=body)

    def upload(self, kind='General', name='doc.pdf'):
        r = self.client.post('/api/documents', data={'company_id': '1', 'kind': kind}, files={'file': (name, PDF, 'application/pdf')})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()['id']

    # ---- per-urgency repair deadlines ----
    def test_sla_deadlines_and_response_tracking(self):
        contract = self.data()['contracts'][0]
        made = {}
        for urgency in ('Emergency', 'Routine'):
            r = self.post('/api/records/repairs', {'company_id': 1, 'contract_id': contract['id'], 'title': f'{urgency} issue', 'category': 'Plumbing', 'urgency': urgency, 'description': 'Something is wrong'})
            self.assertEqual(r.status_code, 200, r.text)
            made[urgency] = next(x for x in self.data()['repairs'] if x['id'] == r.json()['id'])
        due = datetime.fromisoformat(made['Emergency']['response_due_at'])
        self.assertAlmostEqual((due - datetime.now(timezone.utc)).total_seconds() / 3600, 4, delta=0.1)
        self.assertEqual(made['Routine']['due_date'], (date.today() + timedelta(days=42)).isoformat())
        self.assertEqual(made['Emergency']['due_date'], (date.today() + timedelta(days=2)).isoformat())
        self.assertIsNone(made['Emergency']['responded_at'])
        policies = {'Emergency': {'response_hours': 2, 'resolve_days': 1}, 'Urgent': {'response_hours': 24, 'resolve_days': 7}, 'Routine': {'response_hours': 72, 'resolve_days': 30}}
        self.assertEqual(self.post('/api/sla-policies', {'company_id': 1, 'policies': {**policies, 'Emergency': {'response_hours': 100, 'resolve_days': 1}}}).status_code, 422)
        self.assertEqual(self.post('/api/sla-policies', {'company_id': 1, 'policies': policies}).status_code, 200)
        self.assertEqual({p['urgency']: p['response_hours'] for p in self.data()['sla_policies']}, {'Emergency': 2, 'Urgent': 24, 'Routine': 72})
        r = self.post('/api/records/repairs', {'company_id': 1, 'contract_id': contract['id'], 'title': 'Custom SLA', 'category': 'Plumbing', 'urgency': 'Routine', 'description': 'Check policy'})
        new = next(x for x in self.data()['repairs'] if x['id'] == r.json()['id'])
        self.assertEqual(new['due_date'], (date.today() + timedelta(days=30)).isoformat())
        self.assertEqual(self.post(f'/api/repairs/{made["Emergency"]["id"]}/update', {'message': 'On our way', 'tenant_visible': True}).status_code, 200)
        self.assertIsNotNone(next(x for x in self.data()['repairs'] if x['id'] == made['Emergency']['id'])['responded_at'])
        self.login('finance')
        self.assertEqual(self.post('/api/sla-policies', {'company_id': 1, 'policies': policies}).status_code, 403)

    # ---- automation rules ----
    def test_automation_rules_run_once_and_respect_roles(self):
        rule = {'company_id': 1, 'name': 'Nudge at 5 days', 'trigger': 'invoice_overdue', 'days': 5, 'action': 'create_task', 'title': 'Call {tenant} about {invoice} ({amount}, {days} days late)', 'owner': 'Finance team'}
        self.assertEqual(self.post('/api/automation-rules', {**rule, 'trigger': 'drop_table'}).status_code, 422)
        rid = self.post('/api/automation-rules', rule).json()['id']
        dry = self.post('/api/automation-rules/run', {'company_id': 1, 'dry_run': True}).json()
        self.assertGreater(dry['total'], 0)
        before = len(self.data()['tasks'])
        first = self.post('/api/automation-rules/run', {'company_id': 1}).json()
        self.assertEqual(first['total'], dry['total'])
        tasks = self.data()['tasks']
        self.assertEqual(len(tasks), before + first['total'])
        self.assertTrue(any(t['title'].startswith('Call ') and 'days late' in t['title'] and '{' not in t['title'] for t in tasks))
        self.assertEqual(self.post('/api/automation-rules/run', {'company_id': 1}).json()['total'], 0)
        self.assertEqual(self.post(f'/api/automation-rules/{rid}/toggle', {'enabled': False}).status_code, 200)
        self.assertEqual(self.post('/api/automation-rules/run', {'company_id': 1, 'dry_run': True}).json()['total'], 0)
        notify = self.post('/api/automation-rules', {**rule, 'name': 'Tell tenants', 'action': 'notify_tenant', 'title': 'Reminder {invoice}', 'body': 'Please check {amount}.'}).json()['id']
        self.assertGreater(self.post('/api/automation-rules/run', {'company_id': 1}).json()['total'], 0)
        self.login('finance')
        self.assertEqual(self.post('/api/automation-rules', rule).status_code, 403)
        self.assertEqual(self.client.delete(f'/api/automation-rules/{notify}').status_code, 403)
        self.login('manager')
        self.assertEqual(self.client.delete(f'/api/automation-rules/{notify}').status_code, 200)

    # ---- templates and custom fields ----
    def test_templates_merge_fields_custom_fields_and_sharing(self):
        self.assertEqual(self.post('/api/templates/starters', {'company_id': 1}).json()['added'], 3)
        self.assertEqual(self.post('/api/templates/starters', {'company_id': 1}).json()['added'], 0)
        data = self.data()
        contract = data['contracts'][0]
        lease = next(t for t in data['doc_templates'] if t['kind'] == 'Lease')
        pdf = self.post(f'/api/templates/{lease["id"]}/generate', {'contract_id': contract['id']})
        self.assertEqual(pdf.status_code, 200, pdf.text)
        self.assertTrue(pdf.content.startswith(b'%PDF'))
        bad = self.post('/api/templates', {'company_id': 1, 'name': 'Bad', 'kind': 'Letter', 'body': 'Hello {{nope}}'})
        self.assertEqual(bad.status_code, 422)
        tid = self.post('/api/templates', {'company_id': 1, 'name': 'Passport letter', 'kind': 'Letter', 'body': '# Hi\n\nDear {{tenant.name}}, passport {{tenant.passport_no}}.'}).json()['id']
        missing = self.post(f'/api/templates/{tid}/generate', {'contract_id': contract['id']})
        self.assertEqual(missing.status_code, 422)
        self.assertIn('tenant.passport_no', missing.json()['detail'])
        self.assertEqual(self.post('/api/custom-fields', {'company_id': 1, 'entity': 'tenant', 'key': 'name', 'label': 'Dup'}).status_code, 422)
        fid = self.post('/api/custom-fields', {'company_id': 1, 'entity': 'tenant', 'key': 'passport_no', 'label': 'Passport number'}).json()['id']
        self.assertEqual(self.post('/api/custom-values', {'company_id': 1, 'entity': 'tenant', 'entity_id': contract['tenant_id'], 'values': {str(fid): 'NL12345'}}).status_code, 200)
        preview = self.post(f'/api/templates/{tid}/preview', {'contract_id': contract['id']}).json()
        self.assertIn('NL12345', preview['text'])
        self.assertEqual(preview['missing'], [])
        saved = self.post(f'/api/templates/{tid}/generate', {'contract_id': contract['id'], 'save': True, 'share': True})
        self.assertEqual(saved.status_code, 200, saved.text)
        doc = next(d for d in self.data()['documents'] if d['id'] == saved.json()['document_id'])
        self.assertEqual(doc['kind'], 'Generated letter')
        self.assertEqual(doc['shared_count'], 1)
        number = self.post('/api/custom-fields', {'company_id': 1, 'entity': 'contract', 'key': 'parking', 'label': 'Parking spots', 'field_type': 'number'}).json()['id']
        self.assertEqual(self.post('/api/custom-values', {'company_id': 1, 'entity': 'contract', 'entity_id': contract['id'], 'values': {str(number): 'many'}}).status_code, 422)

    # ---- owner documents ----
    def test_owner_documents_share_revoke_and_upload(self):
        owner = self.data()['owners'][0]
        doc = self.upload('General', 'insurance.pdf')
        private = self.upload('supplier invoice', 'invoice.pdf')
        self.assertEqual(self.post('/api/owner-documents', {'company_id': 1, 'owner_id': owner['id'], 'document_id': private, 'category': 'Other'}).status_code, 422)
        self.assertEqual(self.post('/api/owner-documents', {'company_id': 1, 'owner_id': owner['id'], 'document_id': doc, 'category': 'Insurance', 'title': 'Building insurance 2026'}).status_code, 200)
        owner_client = TestClient(app)
        r = owner_client.post('/api/login', headers={'X-Haven-Portal': 'owner'}, json={'email': 'owner@haven.local', 'password': 'HavenDemo!2026'})
        headers = {'X-Haven-Portal': 'owner', 'X-CSRF-Token': r.json()['csrf']}
        docs = owner_client.get('/api/owner/data', headers=headers).json()['documents']
        self.assertEqual([d['title'] for d in docs], ['Building insurance 2026'])
        self.assertEqual(owner_client.get(f'/api/owner/documents/{docs[0]["id"]}/file', headers=headers).content, PDF)
        up = owner_client.post('/api/owner/documents', headers=headers, data={'category': 'Deed / title', 'title': 'Deed of ownership'}, files={'file': ('deed.pdf', PDF, 'application/pdf')})
        self.assertEqual(up.status_code, 200, up.text)
        staff_view = self.data()['owner_documents']
        self.assertEqual(sorted(d['title'] for d in staff_view), ['Building insurance 2026', 'Deed of ownership'])
        shared = next(d for d in staff_view if d['title'] == 'Building insurance 2026')
        self.assertEqual(self.post(f'/api/owner-documents/{shared["id"]}/revoke', {}).status_code, 200)
        self.assertEqual(owner_client.get(f'/api/owner/documents/{shared["id"]}/file', headers=headers).status_code, 404)
        self.assertEqual([d['title'] for d in owner_client.get('/api/owner/data', headers=headers).json()['documents']], ['Deed of ownership'])
        owner_client.close()

    # ---- inbox: thread status, assignment, saved replies ----
    def test_inbox_thread_state_and_saved_replies(self):
        contract = min(self.data()['contracts'], key=lambda c: c['id'])
        tenant = TestClient(app)
        r = tenant.post('/api/login', headers={'X-Haven-Portal': 'tenant'}, json={'email': 'tenant@haven.local', 'password': 'HavenDemo!2026'})
        th = {'X-Haven-Portal': 'tenant', 'X-CSRF-Token': r.json()['csrf']}
        sent = tenant.post('/api/tenant/messages', headers=th, data={'contract_id': str(contract['id']), 'subject': 'Heating question', 'body': 'The radiator is cold.'})
        self.assertEqual(sent.status_code, 200, sent.text)
        tid = sent.json()['id']
        staff = next(u for u in self.data()['staff'] if u['email'] == 'finance@haven.local')
        self.assertEqual(self.post(f'/api/threads/{tid}/state', {'status': 'resolved', 'assignee_id': 999999}).status_code, 422)
        self.assertEqual(self.post(f'/api/threads/{tid}/state', {'status': 'resolved', 'assignee_id': staff['id']}).status_code, 200)
        self.assertEqual([(t['thread_id'], t['status'], t['assignee_id']) for t in self.data()['thread_states']], [(tid, 'resolved', staff['id'])])
        self.assertEqual(tenant.get('/api/tenant/data', headers=th).json()['thread_states'], [{'thread_id': tid, 'status': 'resolved'}])
        again = tenant.post('/api/tenant/messages', headers=th, data={'contract_id': str(contract['id']), 'subject': 'x', 'body': 'Still cold.', 'thread_id': str(tid)})
        self.assertEqual(again.status_code, 200, again.text)
        self.assertEqual(self.data()['thread_states'][0]['status'], 'open')
        solved = tenant.post(f'/api/tenant/threads/{tid}/state', headers=th, json={'status': 'resolved'})
        self.assertEqual(solved.status_code, 200, solved.text)
        self.assertEqual(tenant.get('/api/tenant/data', headers=th).json()['thread_states'], [{'thread_id': tid, 'status': 'resolved'}])
        self.assertEqual(tenant.post('/api/tenant/threads/999999/state', headers=th, json={'status': 'open'}).status_code, 404)
        self.assertEqual(self.post('/api/canned-replies/starters', {'company_id': 1}).json()['added'], 5)
        self.assertEqual(self.post('/api/canned-replies/starters', {'company_id': 1}).json()['added'], 0)
        replies = self.data()['canned_replies']
        self.assertEqual(len(replies), 5)
        self.assertEqual(self.client.delete(f'/api/canned-replies/{replies[0]["id"]}').status_code, 200)
        tenant.close()


if __name__ == '__main__':
    unittest.main()
