import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from app import assistant, db
from app.main import app
from manage import demo


class AssistantTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original_dir = db.DATA_DIR

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        db.DATA_DIR = Path(self.temp.name)
        with contextlib.redirect_stdout(io.StringIO()):
            demo()
        self.client = TestClient(app)
        assistant._hits.clear()
        self.env = mock.patch.dict(os.environ, {'GEMINI_API_KEY': '', 'GROQ_API_KEY': ''})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.client.close()
        db.DATA_DIR = self.original_dir
        self.temp.cleanup()

    def login(self, email, portal=None):
        headers = {'X-Haven-Portal': portal} if portal else {}
        r = self.client.post('/api/login', json={'email': email, 'password': 'HavenDemo!2026'}, headers=headers)
        self.assertEqual(r.status_code, 200, r.text)
        self.headers = dict(headers, **{'X-CSRF-Token': r.json()['csrf']})

    def test_requires_sign_in(self):
        self.assertEqual(self.client.post('/api/assistant/chat', json={'message': 'hi'}).status_code, 401)

    def test_guide_answers_without_key_and_stays_in_audience(self):
        self.login('tenant@haven.local', 'tenant')
        info = self.client.get('/api/assistant/info', headers=self.headers).json()
        self.assertFalse(info['ai'])
        reply = self.client.post('/api/assistant/chat', headers=self.headers, json={'message': 'How do I report a leak?'}).json()
        self.assertEqual(reply['source'], 'guide')
        self.assertEqual(reply['actions'][0]['page'], 'repairs')
        # A tenant is never pointed at a staff-only page.
        reply = self.client.post('/api/assistant/chat', headers=self.headers, json={'message': 'where is automation'}).json()
        self.assertTrue(all(a['page'] in assistant.PAGES['tenant'] for a in reply['actions']))

    def test_gemini_reply_is_used_and_actions_are_validated(self):
        self.login('manager@haven.local')
        sent = {}

        def fake(who, page, history, message, lang='en'):
            sent.update(who=who, page=page, history=history, message=message)
            return 'Receipts are approved there. [[go:payments|Open Payment verification]] [[go:hack|Nope]]'
        with mock.patch.object(assistant, 'ask_gemini', fake):
            data = self.client.post('/api/assistant/chat', headers=self.headers, json={'message': 'verify receipt', 'page': 'overview', 'history': [{'role': 'bot', 'text': 'x'}, {'role': 'user', 'text': 'earlier'}]}).json()
        self.assertEqual(data['source'], 'gemini')
        self.assertEqual(data['actions'], [{'label': 'Open Payment verification', 'page': 'payments'}])
        self.assertNotIn('[[', data['reply'])
        self.assertEqual((sent['who'], sent['page']), ('staff', 'overview'))
        self.assertIn('Answer in Dutch', assistant.system_prompt('staff', 'overview', 'nl'))
        self.assertNotIn('Answer in Dutch', assistant.system_prompt('staff', 'overview', 'en'))
        self.assertEqual(sent['history'], [{'role': 'user', 'text': 'earlier'}])

    def test_falls_back_when_gemini_fails_and_prompt_has_no_private_data(self):
        self.login('manager@haven.local')
        with mock.patch.object(assistant, 'ask_gemini', lambda *a: None):
            data = self.client.post('/api/assistant/chat', headers=self.headers, json={'message': 'where are late payments'}).json()
        self.assertEqual((data['source'], data['actions'][0]['page']), ('guide', 'arrears'))
        prompt = assistant.system_prompt('staff', 'overview')
        self.assertNotIn('haven.local', prompt)

    def test_greetings_get_a_friendly_answer_and_a_failed_ai_is_reported(self):
        self.login('tenant@haven.local', 'tenant')
        hello = self.client.post('/api/assistant/chat', headers=self.headers, json={'message': 'hello'}).json()
        self.assertEqual(hello['reply'], assistant.HELLO)
        self.assertIn('welcome', self.client.post('/api/assistant/chat', headers=self.headers, json={'message': 'thanks!'}).json()['reply'])
        self.assertFalse(hello['ai_unavailable'])                      # no key configured: nothing to report
        with mock.patch.dict(os.environ, {'GEMINI_API_KEY': 'test-key'}), mock.patch.object(assistant, 'ask_gemini', lambda *a: None):
            down = self.client.post('/api/assistant/chat', headers=self.headers, json={'message': 'where do I pay'}).json()
        self.assertTrue(down['ai_unavailable'])                        # key set but Gemini failed: say so
        self.assertEqual(down['source'], 'guide')

    def test_rate_limit(self):
        self.login('manager@haven.local')
        last = None
        for _ in range(assistant.RATE_PER_MINUTE + 1):
            last = self.client.post('/api/assistant/chat', headers=self.headers, json={'message': 'tasks'}).json()
        self.assertIn('quickly', last['reply'])


if __name__ == '__main__':
    unittest.main()
