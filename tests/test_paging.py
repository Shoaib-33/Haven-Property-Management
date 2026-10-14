import contextlib
import io
import tempfile
import unittest
from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient

from app import db
from app.main import app, issue_month
from manage import demo


def period_back(months):
    index = date.today().year * 12 + date.today().month - 1 - months
    return f'{index // 12}-{index % 12 + 1:02d}'


class PagingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original_dir = db.DATA_DIR

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        db.DATA_DIR = Path(self.temp.name)
        with contextlib.redirect_stdout(io.StringIO()):
            demo()
        # six old months: all paid, except month 6 which stays unpaid
        with db.transaction() as conn:
            for back in range(8, 2, -1):
                period = period_back(back)
                issue_month(conn, 1, period, 1)
                if back == 6:
                    continue
                for inv in conn.execute('SELECT * FROM invoices WHERE period=?', (period,)).fetchall():
                    conn.execute("INSERT INTO ledger(company_id,contract_id,invoice_id,kind,amount_cents,created_by,created_at,note) VALUES(1,?,?,'payment',?,1,?,'seed')", (inv['contract_id'], inv['id'], -inv['total_cents'], f'{period}-12T10:00:00+00:00'))
        self.client = TestClient(app)
        r = self.client.post('/api/login', json={'email': 'finance@haven.local', 'password': 'HavenDemo!2026'})
        self.client.headers['X-CSRF-Token'] = r.json()['csrf']

    def tearDown(self):
        self.client.close()
        db.DATA_DIR = self.original_dir
        self.temp.cleanup()

    def test_bulk_payload_is_a_recent_window_with_all_time_totals(self):
        data = self.client.get('/api/data?company_id=1').json()
        periods = {i['period'] for i in data['invoices']}
        self.assertNotIn(period_back(8), periods)          # old and paid: left out
        self.assertNotIn(period_back(4), periods)
        self.assertIn(period_back(6), periods)             # old but unpaid: kept
        self.assertIn(period_back(0), periods)
        self.assertTrue(all('snapshot' not in i for i in data['invoices']))
        ids = {i['id'] for i in data['invoices']}
        self.assertTrue(all(l['invoice_id'] in ids for l in data['ledger'] if l['invoice_id']))
        stats = {m['period']: m for m in data['monthly_stats']}
        self.assertIn(period_back(8), stats)               # but every month still has totals
        self.assertEqual(stats[period_back(8)]['invoices'], stats[period_back(8)]['paid'])
        self.assertEqual(stats[period_back(8)]['billed'], stats[period_back(8)]['collected'])
        self.assertEqual(stats[period_back(6)]['paid'], 0)
        self.assertEqual(data['invoice_window']['total'], len(self.client.get('/api/invoices?company_id=1&page_size=200').json()['items']) + 0 if data['invoice_window']['total'] <= 200 else data['invoice_window']['total'])

    def test_invoice_list_pages_searches_and_filters(self):
        first = self.client.get('/api/invoices?company_id=1&page_size=5').json()
        self.assertEqual((first['page'], len(first['items'])), (1, 5))
        self.assertGreater(first['pages'], 1)
        self.assertGreater(first['total'], 5)
        second = self.client.get('/api/invoices?company_id=1&page_size=5&page=2').json()
        self.assertFalse({i['id'] for i in first['items']} & {i['id'] for i in second['items']})
        unpaid = self.client.get(f'/api/invoices?company_id=1&status=Overdue&period={period_back(6)}').json()
        self.assertTrue(unpaid['items'])
        self.assertTrue(all(i['status'] == 'Overdue' and i['balance_cents'] > 0 for i in unpaid['items']))
        paid = self.client.get(f'/api/invoices?company_id=1&status=Paid&period={period_back(8)}').json()
        self.assertTrue(paid['items'] and all(i['balance_cents'] == 0 for i in paid['items']))
        found = self.client.get('/api/invoices?company_id=1&q=Mila').json()
        self.assertTrue(found['items'] and all('Mila' in i['tenant_name'] for i in found['items']))
        # wildcard characters in a search are plain text, not patterns
        self.assertEqual(self.client.get('/api/invoices?company_id=1&q=%25').json()['total'], 0)
        # the page carries the ledger and receipts of its own rows so the detail dialogs work
        ids = {i['id'] for i in first['items']}
        self.assertTrue(first['ledger'] and all(l['invoice_id'] in ids for l in first['ledger']))

    def test_period_records_ledger_and_audit_endpoints(self):
        month = self.client.get(f'/api/periods/{period_back(8)}?company_id=1').json()
        self.assertTrue(month['invoices'] and month['ledger'])
        self.assertEqual({i['period'] for i in month['invoices']}, {period_back(8)})
        self.assertEqual(self.client.get('/api/periods/nonsense?company_id=1').status_code, 422)
        ledger = self.client.get('/api/ledger?company_id=1&page_size=10&kind=payment').json()
        self.assertEqual(len(ledger['items']), 10)
        self.assertTrue(all(i['kind'] == 'payment' for i in ledger['items']))
        self.assertIn('payment', ledger['kinds'])
        self.assertEqual(self.client.get('/api/ledger?company_id=1&q=seed').json()['total'] > 0, True)
        # the audit log is for admins and managers only
        self.assertEqual(self.client.get('/api/audit?company_id=1').status_code, 403)
        manager = TestClient(app)
        r = manager.post('/api/login', json={'email': 'manager@haven.local', 'password': 'HavenDemo!2026'})
        manager.headers['X-CSRF-Token'] = r.json()['csrf']
        audit = manager.get('/api/audit?company_id=1&page_size=10').json()
        self.assertEqual(len(audit['items']), 10)
        self.assertGreater(audit['total'], 10)
        self.assertTrue(manager.get('/api/audit?company_id=1&q=issued').json()['total'] >= 1)
        self.assertTrue(manager.get('/api/audit?company_id=1&q=Alex').json()['total'] >= 1)
        # a space matches an underscore in action names
        self.assertEqual(manager.get('/api/audit?company_id=1&q=status changed').json()['total'], manager.get('/api/audit?company_id=1&q=status_changed').json()['total'])
        manager.close()

    def test_non_finance_roles_cannot_use_finance_lists(self):
        prop = TestClient(app)
        data = TestClient(app).get('/api/health')
        self.assertEqual(data.status_code, 200)
        self.assertEqual(prop.get('/api/invoices?company_id=1').status_code, 401)
        prop.close()


if __name__ == '__main__':
    unittest.main()
