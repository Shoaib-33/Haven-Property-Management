import contextlib
import io
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from app import db
from app.main import app
from manage import demo


class LeaseOfferTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original_dir = db.DATA_DIR

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        db.DATA_DIR = Path(self.temp.name)
        with contextlib.redirect_stdout(io.StringIO()):
            demo()
        self.staff = TestClient(app)
        r = self.staff.post('/api/login', json={'email': 'manager@haven.local', 'password': 'HavenDemo!2026'})
        self.staff.headers['X-CSRF-Token'] = r.json()['csrf']
        self.tenant = TestClient(app)
        r = self.tenant.post('/api/login', headers={'X-Haven-Portal': 'tenant'}, json={'email': 'tenant@haven.local', 'password': 'HavenDemo!2026'})
        self.th = {'X-Haven-Portal': 'tenant', 'X-CSRF-Token': r.json()['csrf']}
        self.staff.post('/api/templates/starters', json={'company_id': 1})
        data = self.staff.get('/api/data?company_id=1').json()
        self.template = next(t['id'] for t in data['doc_templates'] if t['kind'] == 'Lease')
        self.prop = next(p['id'] for p in data['properties'] if p['name'].startswith('Garden'))
        self.tenant_id = next(t['id'] for t in data['tenants'] if t['email'] == 'emma@example.com')

    def tearDown(self):
        self.staff.close()
        self.tenant.close()
        db.DATA_DIR = self.original_dir
        self.temp.cleanup()

    def offer_body(self, start=None, end=None):
        return {'company_id': 1, 'tenant_id': self.tenant_id, 'property_id': self.prop, 'template_id': self.template,
                'start_date': start or (date.today() + timedelta(days=30)).isoformat(), 'end_date': end, 'basic_rent': '1250', 'service_charge': '80', 'deposit': '2500'}

    def tenant_offers(self):
        return self.tenant.get('/api/tenant/data', headers=self.th).json()['lease_offers']

    def sign(self, oid, name='Emma Janssen', agree=True):
        return self.tenant.post(f'/api/tenant/lease-offers/{oid}/sign', headers=self.th, json={'signer_name': name, 'agree': agree})

    def test_full_flow_send_sign_approve(self):
        sent = self.staff.post('/api/lease-offers', json=self.offer_body())
        self.assertEqual(sent.status_code, 200, sent.text)
        oid = sent.json()['id']
        self.assertEqual([(o['id'], o['status']) for o in self.tenant_offers()], [(oid, 'sent')])
        pdf = self.tenant.get(f'/api/tenant/lease-offers/{oid}/file', headers=self.th)
        self.assertEqual((pdf.status_code, pdf.content[:4]), (200, b'%PDF'))
        # cannot approve before the tenant signs
        self.assertEqual(self.staff.post(f'/api/lease-offers/{oid}/approve', json={'confirmed': True}).status_code, 409)
        # the signature must be the tenant's own name and an explicit agreement
        self.assertEqual(self.sign(oid, 'Someone Else').status_code, 422)
        self.assertEqual(self.sign(oid, agree=False).status_code, 422)
        signed = self.sign(oid, '  emma   janssen ')
        self.assertEqual(signed.status_code, 200, signed.text)
        self.assertEqual(self.tenant_offers()[0]['status'], 'signed')
        self.assertEqual(self.sign(oid).status_code, 409)
        # staff must confirm, then the tenancy exists
        self.assertEqual(self.staff.post(f'/api/lease-offers/{oid}/approve', json={}).status_code, 422)
        approved = self.staff.post(f'/api/lease-offers/{oid}/approve', json={'confirmed': True})
        self.assertEqual(approved.status_code, 200, approved.text)
        contract_id = approved.json()['contract_id']
        data = self.staff.get('/api/data?company_id=1').json()
        contract = next(c for c in data['contracts'] if c['id'] == contract_id)
        self.assertEqual((contract['status'], contract['property_id'], contract['deposit_cents']), ('active', self.prop, 250000))
        rent = next(r for r in data['rent_versions'] if r['contract_id'] == contract_id)
        self.assertEqual((rent['basic_cents'], rent['service_cents']), (125000, 8000))
        self.assertEqual(next(o for o in data['lease_offers'] if o['id'] == oid)['status'], 'approved')
        tenant_data = self.tenant.get('/api/tenant/data', headers=self.th).json()
        self.assertIn(contract_id, [c['id'] for c in tenant_data['contracts']])
        final = [d for d in tenant_data['documents'] if d['kind'] == 'Contract']
        self.assertTrue(final)
        self.assertEqual(self.tenant.get(f'/api/tenant/documents/{final[0]["id"]}/file', headers=self.th).content[:4], b'%PDF')

    def test_overlap_return_decline_and_permissions(self):
        first = self.staff.post('/api/lease-offers', json=self.offer_body()).json()['id']
        self.assertEqual(self.staff.post('/api/lease-offers', json=self.offer_body()).status_code, 409)
        self.sign(first)
        self.assertEqual(self.staff.post(f'/api/lease-offers/{first}/return', json={}).status_code, 422)
        back = self.staff.post(f'/api/lease-offers/{first}/return', json={'note': 'Please check the start date.'})
        self.assertEqual(back.status_code, 200, back.text)
        offer = self.tenant_offers()[0]
        self.assertEqual((offer['status'], offer['response_note']), ('returned', 'Please check the start date.'))
        self.assertEqual(self.sign(first).status_code, 200)
        self.assertEqual(self.tenant.post(f'/api/tenant/lease-offers/{first}/decline', headers=self.th, json={'reason': 'Changed my mind'}).status_code, 200)
        self.assertEqual(self.tenant_offers()[0]['status'], 'declined')
        # a declined offer frees the unit again, and a withdrawn one disappears for the tenant
        second = self.staff.post('/api/lease-offers', json=self.offer_body())
        self.assertEqual(second.status_code, 200, second.text)
        second_id = second.json()['id']
        self.assertEqual(self.staff.post(f'/api/lease-offers/{second_id}/withdraw', json={}).status_code, 200)
        self.assertNotIn(second_id, [o['id'] for o in self.tenant_offers()])
        # finance cannot send contracts
        fin = TestClient(app)
        r = fin.post('/api/login', json={'email': 'finance@haven.local', 'password': 'HavenDemo!2026'})
        fin.headers['X-CSRF-Token'] = r.json()['csrf']
        self.assertEqual(fin.post('/api/lease-offers', json=self.offer_body()).status_code, 403)
        fin.close()

    def test_tenant_without_portal_login_is_refused(self):
        data = self.staff.get('/api/data?company_id=1').json()
        body = self.offer_body()
        body['tenant_id'] = next(t['id'] for t in data['tenants'] if t['email'] == 'mila@example.com')
        r = self.staff.post('/api/lease-offers', json=body)
        self.assertEqual(r.status_code, 422)
        self.assertIn('portal login', r.json()['detail'])


    def signature_png(self, blank=False):
        import base64
        from PIL import Image, ImageDraw
        image = Image.new('RGBA', (300, 100), (255, 255, 255, 0))
        if not blank:
            ImageDraw.Draw(image).line([(10, 70), (60, 20), (110, 80), (170, 25), (260, 60)], fill=(20, 33, 61, 255), width=4)
        buffer = io.BytesIO()
        image.save(buffer, 'PNG')
        return 'data:image/png;base64,' + base64.b64encode(buffer.getvalue()).decode()

    def test_form_edited_text_and_drawn_signatures_end_to_end(self):
        body = self.offer_body()
        preview = self.staff.post('/api/lease-offers/preview', json=body)
        self.assertEqual(preview.status_code, 200, preview.text)
        text = preview.json()['body'] + ' Special condition: no pets in the apartment.'
        asked = [{'key': 'phone'}, {'key': 'id_number'}, {'key': 'privacy'}, {'label': 'Licence plate', 'required': False}]
        sent = self.staff.post('/api/lease-offers', json={**body, 'body': text, 'form_fields': asked})
        self.assertEqual(sent.status_code, 200, sent.text)
        oid = sent.json()['id']
        offer = self.tenant_offers()[0]
        self.assertIn('no pets', offer['body'])
        self.assertEqual([f['key'] for f in offer['form_fields']], ['phone', 'id_number', 'privacy', 'custom_1'])
        self.assertNotIn('tenant_signature', offer)
        good = {'agree': True, 'signature': self.signature_png(), 'signature_mode': 'draw', 'form': {'phone': '+31 6 1234 5678', 'id_number': 'NL123456', 'privacy': True}}
        sign = lambda **change: self.tenant.post(f'/api/tenant/lease-offers/{oid}/sign', headers=self.th, json={**good, **change})
        self.assertEqual(sign(signature=None).status_code, 422)                      # a signature is needed
        self.assertEqual(sign(signature=self.signature_png(blank=True)).status_code, 422)   # an empty pad is not a signature
        self.assertEqual(sign(signature='data:image/png;base64,AAAA').status_code, 422)
        self.assertEqual(sign(form={'phone': '+31 6 1234 5678', 'privacy': True}).status_code, 422)  # required answer missing
        self.assertEqual(sign(form={**good['form'], 'privacy': False}).status_code, 422)
        self.assertEqual(sign(agree=False).status_code, 422)
        signed = sign()
        self.assertEqual(signed.status_code, 200, signed.text)
        # the PDF people open right after signing already shows the tenant's details and signature, and the landlord's pending line
        import fitz
        for response in (self.tenant.get(f'/api/tenant/lease-offers/{oid}/file', headers=self.th), self.staff.get(f'/api/lease-offers/{oid}/file')):
            shown = fitz.open(stream=response.content)
            shown_text = ' '.join(page.get_text() for page in shown)
            self.assertIn('+31 6 1234 5678', shown_text)
            self.assertIn('Nog te ondertekenen', shown_text)
            self.assertGreaterEqual(sum(len(page.get_image_info()) for page in shown), 1)
        review = self.staff.get(f'/api/lease-offers/{oid}/review').json()
        self.assertEqual(review['values']['id_number'], 'NL123456')
        self.assertTrue(review['signature'].startswith('data:image/png;base64,'))
        self.assertTrue(review['edited'])
        staff_list = next(o for o in self.staff.get('/api/data?company_id=1').json()['lease_offers'] if o['id'] == oid)
        self.assertNotIn('tenant_signature', staff_list)
        # returned: the signature is cleared, the answers stay so the tenant only corrects what is needed
        self.assertEqual(self.staff.post(f'/api/lease-offers/{oid}/return', json={'note': 'Check the phone number'}).status_code, 200)
        again = self.tenant_offers()[0]
        self.assertEqual(again['form_values']['phone'], '+31 6 1234 5678')
        self.assertEqual(sign(form={**good['form'], 'phone': '+31 6 9999 0000'}).status_code, 200)
        approved = self.staff.post(f'/api/lease-offers/{oid}/approve', json={'confirmed': True, 'signature': self.signature_png(), 'signature_mode': 'type'})
        self.assertEqual(approved.status_code, 200, approved.text)
        after = fitz.open(stream=self.staff.get(f'/api/lease-offers/{oid}/file').content)
        self.assertGreaterEqual(sum(len(page.get_image_info()) for page in after), 2)
        final = next(d for d in self.tenant.get('/api/tenant/data', headers=self.th).json()['documents'] if d['kind'] == 'Contract')
        pdf = self.tenant.get(f"/api/tenant/documents/{final['id']}/file", headers=self.th).content
        import fitz
        doc = fitz.open(stream=pdf)
        text_out = ' '.join(page.get_text() for page in doc)
        self.assertIn('+31 6 9999 0000', text_out)
        self.assertIn('Signatures', text_out)
        self.assertGreaterEqual(sum(len(page.get_image_info()) for page in doc), 2)       # tenant and landlord signature images

    def test_legacy_typed_name_still_signs_an_offer_without_a_form(self):
        oid = self.staff.post('/api/lease-offers', json=self.offer_body()).json()['id']
        self.assertIsNone(self.tenant_offers()[0]['form_fields'])
        self.assertEqual(self.sign(oid).status_code, 200)

    def test_landlord_signature_is_on_the_contract_when_it_is_sent(self):
        sig = self.signature_png()
        saved = self.staff.post('/api/companies/1/signature', json={'name': 'Alex Morgan', 'title': 'Director', 'signature': sig, 'signature_mode': 'type'})
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual(self.staff.post('/api/companies/1/signature', json={'name': 'X', 'signature': 'data:image/png;base64,AAAA'}).status_code, 422)
        shown = self.staff.get('/api/companies/1/signature').json()
        self.assertEqual((shown['set'], shown['name'], shown['title']), (True, 'Alex Morgan', 'Director'))
        oid = self.staff.post('/api/lease-offers', json=self.offer_body()).json()['id']
        import fitz
        draft = fitz.open(stream=self.tenant.get(f'/api/tenant/lease-offers/{oid}/file', headers=self.th).content)
        draft_text = ' '.join(page.get_text() for page in draft)
        self.assertIn('Alex Morgan', draft_text)
        self.assertIn('Director', draft_text)
        self.assertIn('Te ondertekenen door de huurder', draft_text)
        self.assertEqual(sum(len(page.get_image_info()) for page in draft), 1)       # the landlord's signature only
        self.assertTrue(next(o for o in self.staff.get('/api/data?company_id=1').json()['lease_offers'] if o['id'] == oid)['landlord_signed'])
        self.assertEqual(self.sign(oid).status_code, 200)
        # approving needs no second landlord signature
        approved = self.staff.post(f'/api/lease-offers/{oid}/approve', json={'confirmed': True})
        self.assertEqual(approved.status_code, 200, approved.text)
        final = fitz.open(stream=self.staff.get(f'/api/lease-offers/{oid}/file').content)
        self.assertEqual(sum(len(page.get_image_info()) for page in final), 1)       # an older-style tenant has typed their name, so there is no tenant image
        self.assertEqual(self.staff.delete('/api/companies/1/signature').status_code, 200)
        self.assertFalse(self.staff.get('/api/companies/1/signature').json()['set'])

if __name__ == '__main__':
    unittest.main()
