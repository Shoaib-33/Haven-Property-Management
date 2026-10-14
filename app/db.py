import hashlib
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

DATA_DIR = Path(os.environ.get('HAVEN_DATA_DIR', Path(__file__).resolve().parent.parent / 'data'))


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def connect():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DATA_DIR / 'haven.db', timeout=20, factory=ClosingConnection)
    connection.row_factory = sqlite3.Row
    connection.execute('PRAGMA foreign_keys = ON')
    return connection


@contextmanager
def transaction():
    conn = connect()
    conn.pending_files = []
    try:
        conn.execute('BEGIN IMMEDIATE')
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        # Files written during a rolled-back transaction would otherwise be orphaned.
        for path in conn.pending_files:
            Path(path).unlink(missing_ok=True)
        raise
    finally:
        conn.close()


def audit_view(user_id, company_id, entity, entity_id, detail=None, minutes=10):
    """Record a page view at most once per window so read-only requests rarely take the write lock."""
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat(timespec='seconds')
    with connect() as conn:
        recent = conn.execute('SELECT 1 FROM audit WHERE user_id=? AND action="viewed" AND entity=? AND company_id IS ? AND created_at>? LIMIT 1', (user_id, entity, company_id, cutoff)).fetchone()
    if recent:
        return
    with transaction() as conn:
        audit(conn, user_id, company_id, 'viewed', entity, entity_id, detail or {})


def audit(conn, user_id, company_id, action, entity, entity_id, detail):
    previous = conn.execute('SELECT hash FROM audit ORDER BY id DESC LIMIT 1').fetchone()
    prev_hash = previous['hash'] if previous else '0' * 64
    stamp = now()
    payload = json.dumps([stamp, user_id, company_id, action, entity, entity_id, detail], sort_keys=True, separators=(',', ':'))
    digest = hashlib.sha256((prev_hash + payload).encode()).hexdigest()
    conn.execute('INSERT INTO audit (created_at,user_id,company_id,action,entity,entity_id,detail,payload,previous_hash,hash) VALUES (?,?,?,?,?,?,?,?,?,?)',
                 (stamp, user_id, company_id, action, entity, entity_id, json.dumps(detail), payload, prev_hash, digest))


SCHEMA = '''
CREATE TABLE IF NOT EXISTS users (
 id INTEGER PRIMARY KEY, name TEXT NOT NULL, email TEXT NOT NULL UNIQUE COLLATE NOCASE,
 password_hash TEXT NOT NULL, global_admin INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS companies (
 id INTEGER PRIMARY KEY, name TEXT NOT NULL, kvk TEXT NOT NULL DEFAULT '', address TEXT NOT NULL DEFAULT '',
 payment_instructions TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS memberships (
 user_id INTEGER REFERENCES users(id), company_id INTEGER REFERENCES companies(id),
 role TEXT NOT NULL CHECK(role IN ('admin','manager','finance','property','inspector')), PRIMARY KEY(user_id,company_id)
);
CREATE TABLE IF NOT EXISTS sessions (
 token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), expires_at TEXT NOT NULL, csrf TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS login_attempts (key TEXT PRIMARY KEY, attempts INTEGER NOT NULL, blocked_until REAL NOT NULL);
CREATE TABLE IF NOT EXISTS properties (
 id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id), name TEXT NOT NULL,
 address TEXT NOT NULL, city TEXT NOT NULL, postcode TEXT NOT NULL DEFAULT '', kind TEXT NOT NULL DEFAULT 'Apartment',
 rooms INTEGER NOT NULL DEFAULT 1, area REAL NOT NULL DEFAULT 0, energy_label TEXT NOT NULL DEFAULT 'Unknown',
 bag_id TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tenants (
 id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id), name TEXT NOT NULL,
 email TEXT NOT NULL, phone TEXT NOT NULL DEFAULT '', language TEXT NOT NULL DEFAULT 'nl', created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS contracts (
 id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id), property_id INTEGER NOT NULL REFERENCES properties(id),
 tenant_id INTEGER NOT NULL REFERENCES tenants(id), signed_on TEXT NOT NULL, start_date TEXT NOT NULL, end_date TEXT,
 status TEXT NOT NULL DEFAULT 'active', deposit_cents INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS rent_versions (
 id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id), contract_id INTEGER NOT NULL REFERENCES contracts(id),
 effective_from TEXT NOT NULL, basic_cents INTEGER NOT NULL CHECK(basic_cents>=0), service_cents INTEGER NOT NULL CHECK(service_cents>=0),
 created_at TEXT NOT NULL, created_by INTEGER REFERENCES users(id), UNIQUE(contract_id,effective_from)
);
CREATE TABLE IF NOT EXISTS invoices (
 id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id), contract_id INTEGER NOT NULL REFERENCES contracts(id),
 number TEXT NOT NULL, period TEXT NOT NULL, due_date TEXT NOT NULL, basic_cents INTEGER NOT NULL, service_cents INTEGER NOT NULL,
 total_cents INTEGER NOT NULL CHECK(total_cents>0), snapshot TEXT NOT NULL, created_at TEXT NOT NULL,
 UNIQUE(company_id,number), UNIQUE(contract_id,period)
);
CREATE TABLE IF NOT EXISTS upload_links (
 id INTEGER PRIMARY KEY, invoice_id INTEGER NOT NULL REFERENCES invoices(id), token_hash TEXT NOT NULL UNIQUE,
 expires_at TEXT NOT NULL, revoked INTEGER NOT NULL DEFAULT 0, created_by INTEGER REFERENCES users(id)
);
CREATE TABLE IF NOT EXISTS documents (
 id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id), contract_id INTEGER REFERENCES contracts(id),
 name TEXT NOT NULL, kind TEXT NOT NULL, mime TEXT NOT NULL, size INTEGER NOT NULL, sha256 TEXT NOT NULL,
 file_key TEXT NOT NULL UNIQUE, created_by INTEGER REFERENCES users(id), created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS payment_proofs (
 id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id), invoice_id INTEGER NOT NULL REFERENCES invoices(id),
 document_id INTEGER NOT NULL REFERENCES documents(id), amount_cents INTEGER NOT NULL CHECK(amount_cents>0),
 paid_on TEXT NOT NULL, method TEXT NOT NULL, reference TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '',
 status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','approved','rejected','reversed')),
 submitted_by INTEGER REFERENCES users(id), reviewed_by INTEGER REFERENCES users(id), review_note TEXT,
 created_at TEXT NOT NULL, reviewed_at TEXT
);
CREATE TABLE IF NOT EXISTS ledger (
 id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id), contract_id INTEGER NOT NULL REFERENCES contracts(id),
 invoice_id INTEGER REFERENCES invoices(id), proof_id INTEGER REFERENCES payment_proofs(id),
 kind TEXT NOT NULL, amount_cents INTEGER NOT NULL, reverses_id INTEGER UNIQUE REFERENCES ledger(id),
 created_by INTEGER REFERENCES users(id), created_at TEXT NOT NULL, note TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS one_proof_posting ON ledger(proof_id) WHERE kind='payment';
CREATE TABLE IF NOT EXISTS deposit_events (
 id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id), contract_id INTEGER NOT NULL REFERENCES contracts(id),
 kind TEXT NOT NULL, amount_cents INTEGER NOT NULL CHECK(amount_cents>0), status TEXT NOT NULL,
 reason TEXT NOT NULL, document_id INTEGER REFERENCES documents(id), created_by INTEGER NOT NULL REFERENCES users(id),
 approved_by INTEGER REFERENCES users(id), created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS repairs (
 id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id), contract_id INTEGER NOT NULL REFERENCES contracts(id),
 title TEXT NOT NULL, category TEXT NOT NULL, urgency TEXT NOT NULL, description TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'received', reported_on TEXT NOT NULL, written INTEGER NOT NULL DEFAULT 1,
 due_date TEXT NOT NULL, assigned_to TEXT NOT NULL DEFAULT '', completion_note TEXT,
 completion_document_id INTEGER REFERENCES documents(id), completed_at TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tasks (
 id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id), contract_id INTEGER REFERENCES contracts(id),
 title TEXT NOT NULL, due_date TEXT NOT NULL, owner TEXT NOT NULL, category TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'open', created_at TEXT NOT NULL, UNIQUE(company_id,contract_id,title,due_date)
);
CREATE TABLE IF NOT EXISTS communications (
 id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id), contract_id INTEGER NOT NULL REFERENCES contracts(id),
 channel TEXT NOT NULL, direction TEXT NOT NULL, subject TEXT NOT NULL, body TEXT NOT NULL,
 status TEXT NOT NULL, created_by INTEGER NOT NULL REFERENCES users(id), created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS valuations (
 id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id), property_id INTEGER NOT NULL REFERENCES properties(id),
 points REAL NOT NULL, max_rent_cents INTEGER NOT NULL, reference_date TEXT NOT NULL, source TEXT NOT NULL,
 document_id INTEGER NOT NULL REFERENCES documents(id), created_by INTEGER NOT NULL REFERENCES users(id), created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS inspections (
 id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id), contract_id INTEGER NOT NULL REFERENCES contracts(id),
 kind TEXT NOT NULL, inspected_on TEXT NOT NULL, findings TEXT NOT NULL, document_id INTEGER REFERENCES documents(id),
 status TEXT NOT NULL DEFAULT 'draft', signature_name TEXT, created_by INTEGER NOT NULL REFERENCES users(id), created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cost_lines (
 id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id), property_id INTEGER NOT NULL REFERENCES properties(id),
 year INTEGER NOT NULL, category TEXT NOT NULL, supplier TEXT NOT NULL, amount_cents INTEGER NOT NULL CHECK(amount_cents>0),
 document_id INTEGER NOT NULL REFERENCES documents(id), created_by INTEGER NOT NULL REFERENCES users(id), created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settlements (
 id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id), property_id INTEGER NOT NULL REFERENCES properties(id),
 year INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'draft', snapshot TEXT NOT NULL,
 created_by INTEGER NOT NULL REFERENCES users(id), approved_by INTEGER REFERENCES users(id), created_at TEXT NOT NULL,
 UNIQUE(property_id,year)
);
CREATE TABLE IF NOT EXISTS rent_proposals (
 id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id), contract_id INTEGER NOT NULL REFERENCES contracts(id),
 effective_from TEXT NOT NULL, basic_cents INTEGER NOT NULL CHECK(basic_cents>0), service_cents INTEGER NOT NULL CHECK(service_cents>=0),
 reason TEXT NOT NULL, source TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
 created_by INTEGER NOT NULL REFERENCES users(id), approved_by INTEGER REFERENCES users(id), created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS rules (
 id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id), name TEXT NOT NULL, value TEXT NOT NULL,
 effective_from TEXT NOT NULL, source TEXT NOT NULL, created_by INTEGER NOT NULL REFERENCES users(id), created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit (
 id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, user_id INTEGER REFERENCES users(id), company_id INTEGER REFERENCES companies(id),
 action TEXT NOT NULL, entity TEXT NOT NULL, entity_id INTEGER, detail TEXT NOT NULL, payload TEXT NOT NULL,
 previous_hash TEXT NOT NULL, hash TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS ledger_no_update BEFORE UPDATE ON ledger BEGIN SELECT RAISE(ABORT,'Ledger entries are immutable'); END;
CREATE TRIGGER IF NOT EXISTS ledger_no_delete BEFORE DELETE ON ledger BEGIN SELECT RAISE(ABORT,'Ledger entries are immutable'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit BEGIN SELECT RAISE(ABORT,'Audit events are immutable'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit BEGIN SELECT RAISE(ABORT,'Audit events are immutable'); END;
CREATE TRIGGER IF NOT EXISTS invoice_no_update BEFORE UPDATE ON invoices BEGIN SELECT RAISE(ABORT,'Issued invoices are immutable'); END;
CREATE TRIGGER IF NOT EXISTS invoice_no_delete BEFORE DELETE ON invoices BEGIN SELECT RAISE(ABORT,'Issued invoices are immutable'); END;
CREATE TRIGGER IF NOT EXISTS rent_no_update BEFORE UPDATE ON rent_versions BEGIN SELECT RAISE(ABORT,'Rent history is immutable'); END;
CREATE TRIGGER IF NOT EXISTS rent_no_delete BEFORE DELETE ON rent_versions BEGIN SELECT RAISE(ABORT,'Rent history is immutable'); END;
CREATE TRIGGER IF NOT EXISTS contract_identity_locked BEFORE UPDATE OF signed_on,company_id,property_id,tenant_id,start_date ON contracts BEGIN SELECT RAISE(ABORT,'Contract identity is locked'); END;
CREATE INDEX IF NOT EXISTS properties_company ON properties(company_id);
CREATE INDEX IF NOT EXISTS tenants_company ON tenants(company_id);
CREATE INDEX IF NOT EXISTS contracts_company ON contracts(company_id);
CREATE INDEX IF NOT EXISTS audit_company ON audit(company_id,id);
CREATE INDEX IF NOT EXISTS ledger_invoice ON ledger(invoice_id);
'''


def init_db():
    with connect() as conn:
        conn.execute('PRAGMA journal_mode=WAL')
        conn.executescript(SCHEMA)
        from .migrations import migrate
        migrate(conn)
    (DATA_DIR / 'uploads').mkdir(exist_ok=True)
