"""Additive migrations; existing tenant and financial records are never reseeded."""

V2 = '''
CREATE TABLE portal_accounts (
 user_id INTEGER NOT NULL REFERENCES users(id), tenant_id INTEGER NOT NULL REFERENCES tenants(id),
 company_id INTEGER NOT NULL REFERENCES companies(id), enabled INTEGER NOT NULL DEFAULT 1,
 created_at TEXT NOT NULL, PRIMARY KEY(user_id,tenant_id), UNIQUE(tenant_id)
);
CREATE TABLE contractors (
 id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id), name TEXT NOT NULL,
 email TEXT NOT NULL, phone TEXT NOT NULL DEFAULT '', trade TEXT NOT NULL,
 agreement_document_id INTEGER REFERENCES documents(id), active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL
);
CREATE TABLE contractor_accounts (
 user_id INTEGER NOT NULL REFERENCES users(id), contractor_id INTEGER NOT NULL UNIQUE REFERENCES contractors(id),
 company_id INTEGER NOT NULL REFERENCES companies(id), enabled INTEGER NOT NULL DEFAULT 1,
 created_at TEXT NOT NULL, PRIMARY KEY(user_id,contractor_id)
);
CREATE TABLE account_invitations (
 id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id), email TEXT NOT NULL,
 name TEXT NOT NULL, kind TEXT NOT NULL CHECK(kind IN ('tenant','contractor')),
 target_id INTEGER NOT NULL, token_hash TEXT NOT NULL UNIQUE, expires_at TEXT NOT NULL,
 used_at TEXT, revoked INTEGER NOT NULL DEFAULT 0, created_by INTEGER NOT NULL REFERENCES users(id), created_at TEXT NOT NULL
);
CREATE TABLE shared_documents (
 id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id), contract_id INTEGER NOT NULL REFERENCES contracts(id),
 document_id INTEGER NOT NULL REFERENCES documents(id), shared_by INTEGER NOT NULL REFERENCES users(id),
 shared_at TEXT NOT NULL, revoked INTEGER NOT NULL DEFAULT 0, UNIQUE(document_id,contract_id)
);
CREATE TABLE portal_messages (
 id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id), contract_id INTEGER NOT NULL REFERENCES contracts(id),
 sender_id INTEGER NOT NULL REFERENCES users(id), subject TEXT NOT NULL, body TEXT NOT NULL,
 document_id INTEGER REFERENCES documents(id), created_at TEXT NOT NULL
);
CREATE TABLE requests (
 id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id), contract_id INTEGER NOT NULL REFERENCES contracts(id),
 kind TEXT NOT NULL, subject TEXT NOT NULL, body TEXT NOT NULL, requested_date TEXT,
 status TEXT NOT NULL DEFAULT 'open', response TEXT, responded_by INTEGER REFERENCES users(id),
 created_by INTEGER NOT NULL REFERENCES users(id), created_at TEXT NOT NULL, responded_at TEXT
);
CREATE TABLE notifications (
 id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id), user_id INTEGER NOT NULL REFERENCES users(id),
 title TEXT NOT NULL, body TEXT NOT NULL, target TEXT NOT NULL, is_read INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
);
CREATE TABLE repair_updates (
 id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id), repair_id INTEGER NOT NULL REFERENCES repairs(id),
 user_id INTEGER NOT NULL REFERENCES users(id), message TEXT NOT NULL, document_id INTEGER REFERENCES documents(id),
 tenant_visible INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
);
CREATE TABLE work_orders (
 id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id), repair_id INTEGER NOT NULL REFERENCES repairs(id),
 contractor_id INTEGER NOT NULL REFERENCES contractors(id), scope TEXT NOT NULL, scheduled_for TEXT,
 status TEXT NOT NULL DEFAULT 'assigned', quote_cents INTEGER, quote_note TEXT, quote_status TEXT NOT NULL DEFAULT 'none',
 quoted_by INTEGER REFERENCES users(id), approved_by INTEGER REFERENCES users(id),
 completion_note TEXT, completion_document_id INTEGER REFERENCES documents(id),
 invoice_document_id INTEGER REFERENCES documents(id), completed_at TEXT,
 created_by INTEGER NOT NULL REFERENCES users(id), created_at TEXT NOT NULL
);
CREATE TABLE credit_notes (
 id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id), invoice_id INTEGER NOT NULL REFERENCES invoices(id),
 amount_cents INTEGER NOT NULL CHECK(amount_cents>0), reason TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
 created_by INTEGER NOT NULL REFERENCES users(id), reviewed_by INTEGER REFERENCES users(id), review_note TEXT,
 created_at TEXT NOT NULL, reviewed_at TEXT
);
CREATE TABLE payment_plans (
 id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id), contract_id INTEGER NOT NULL REFERENCES contracts(id),
 title TEXT NOT NULL, total_cents INTEGER NOT NULL CHECK(total_cents>0), instalment_cents INTEGER NOT NULL CHECK(instalment_cents>0),
 first_due TEXT NOT NULL, interval_days INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
 created_by INTEGER NOT NULL REFERENCES users(id), approved_by INTEGER REFERENCES users(id), created_at TEXT NOT NULL
);
CREATE TABLE plan_instalments (
 id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id), plan_id INTEGER NOT NULL REFERENCES payment_plans(id),
 due_date TEXT NOT NULL, amount_cents INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'open',
 payment_proof_id INTEGER REFERENCES payment_proofs(id), paid_at TEXT
);
CREATE TABLE published_statements (
 id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id), settlement_id INTEGER NOT NULL REFERENCES settlements(id),
 contract_id INTEGER NOT NULL REFERENCES contracts(id), document_id INTEGER NOT NULL REFERENCES documents(id),
 published_by INTEGER NOT NULL REFERENCES users(id), published_at TEXT NOT NULL,
 UNIQUE(settlement_id,contract_id)
);
CREATE TABLE job_runs (
 id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id), run_on TEXT NOT NULL,
 created_at TEXT NOT NULL, result TEXT NOT NULL, UNIQUE(company_id,run_on)
);
CREATE INDEX portal_contract_access ON portal_accounts(user_id,tenant_id,enabled);
CREATE INDEX portal_messages_contract ON portal_messages(contract_id,id);
CREATE INDEX notification_inbox ON notifications(user_id,is_read);
CREATE INDEX work_order_contractor ON work_orders(contractor_id,status);
CREATE TRIGGER portal_message_no_update BEFORE UPDATE ON portal_messages BEGIN SELECT RAISE(ABORT,'Messages are immutable'); END;
CREATE TRIGGER portal_message_no_delete BEFORE DELETE ON portal_messages BEGIN SELECT RAISE(ABORT,'Messages are immutable'); END;
CREATE TRIGGER repair_update_no_update BEFORE UPDATE ON repair_updates BEGIN SELECT RAISE(ABORT,'Repair updates are immutable'); END;
CREATE TRIGGER repair_update_no_delete BEFORE DELETE ON repair_updates BEGIN SELECT RAISE(ABORT,'Repair updates are immutable'); END;
'''

V3 = '''
CREATE UNIQUE INDEX installment_one_payment ON plan_instalments(payment_proof_id) WHERE payment_proof_id IS NOT NULL;
CREATE INDEX invoice_period_company ON invoices(company_id,period);
CREATE INDEX proof_invoice_status ON payment_proofs(invoice_id,status);
CREATE INDEX task_due_company ON tasks(company_id,status,due_date);
CREATE INDEX repairs_company_status ON repairs(company_id,status,due_date);
CREATE TRIGGER credit_note_no_delete BEFORE DELETE ON credit_notes BEGIN SELECT RAISE(ABORT,'Credit notes are immutable'); END;
'''

V4 = '''
CREATE TABLE inspector_assignments (
 user_id INTEGER NOT NULL REFERENCES users(id), company_id INTEGER NOT NULL REFERENCES companies(id),
 property_id INTEGER NOT NULL REFERENCES properties(id), assigned_by INTEGER NOT NULL REFERENCES users(id),
 assigned_at TEXT NOT NULL, PRIMARY KEY(user_id,property_id)
);
CREATE INDEX inspector_property_access ON inspector_assignments(company_id,user_id,property_id);
'''

V5 = '''
CREATE TABLE contractor_agreement_submissions (
 id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id),
 contractor_id INTEGER NOT NULL REFERENCES contractors(id), document_id INTEGER NOT NULL REFERENCES documents(id),
 status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','approved','rejected')),
 submitted_at TEXT NOT NULL, reviewed_by INTEGER REFERENCES users(id), reviewed_at TEXT, review_note TEXT NOT NULL DEFAULT ''
);
CREATE INDEX contractor_agreement_review ON contractor_agreement_submissions(company_id,status,id);
'''

V6 = '''
CREATE TABLE arrears_cases (
 id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id),
 contract_id INTEGER NOT NULL REFERENCES contracts(id), invoice_id INTEGER NOT NULL UNIQUE REFERENCES invoices(id),
 status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','paused','resolved','closed')),
 paused_until TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE arrears_events (
 id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id), case_id INTEGER NOT NULL REFERENCES arrears_cases(id),
 step TEXT NOT NULL, occurred_on TEXT NOT NULL, received_on TEXT, note TEXT NOT NULL DEFAULT '',
 created_by INTEGER REFERENCES users(id), created_at TEXT NOT NULL
);
CREATE INDEX arrears_cases_company_status ON arrears_cases(company_id,status);
CREATE INDEX arrears_events_case ON arrears_events(case_id,id);
'''

V7 = '''
ALTER TABLE portal_messages ADD COLUMN thread_id INTEGER REFERENCES portal_messages(id);
CREATE INDEX portal_messages_thread ON portal_messages(contract_id,thread_id,id);
'''

V8 = '''
CREATE TABLE outbound_email_jobs (
 id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id),
 contract_id INTEGER NOT NULL REFERENCES contracts(id), invoice_id INTEGER NOT NULL REFERENCES invoices(id),
 kind TEXT NOT NULL CHECK(kind IN ('overdue_friendly','overdue_followup')),
 recipient TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','sending','sent','failed','cancelled')),
 attempts INTEGER NOT NULL DEFAULT 0, next_attempt_at TEXT NOT NULL, last_error TEXT,
 created_at TEXT NOT NULL, sent_at TEXT, UNIQUE(invoice_id,kind)
);
CREATE INDEX outbound_email_queue ON outbound_email_jobs(status,next_attempt_at,id);
CREATE INDEX outbound_email_company ON outbound_email_jobs(company_id,invoice_id,id);
'''

V9 = '''
ALTER TABLE outbound_email_jobs RENAME TO outbound_email_jobs_v8;
CREATE TABLE outbound_email_jobs (
 id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id),
 contract_id INTEGER NOT NULL REFERENCES contracts(id), invoice_id INTEGER NOT NULL REFERENCES invoices(id),
 kind TEXT NOT NULL CHECK(kind IN ('invoice_issued','overdue_friendly','overdue_followup')),
 recipient TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','sending','sent','failed','cancelled')),
 attempts INTEGER NOT NULL DEFAULT 0, next_attempt_at TEXT NOT NULL, last_error TEXT,
 created_at TEXT NOT NULL, sent_at TEXT, UNIQUE(invoice_id,kind)
);
INSERT INTO outbound_email_jobs SELECT * FROM outbound_email_jobs_v8;
DROP TABLE outbound_email_jobs_v8;
CREATE INDEX outbound_email_queue ON outbound_email_jobs(status,next_attempt_at,id);
CREATE INDEX outbound_email_company ON outbound_email_jobs(company_id,invoice_id,id);
'''

V10 = '''
CREATE TABLE service_charge_groups (
 id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id), name TEXT NOT NULL,
 created_by INTEGER NOT NULL REFERENCES users(id), created_at TEXT NOT NULL
);
CREATE TABLE service_charge_group_units (
 group_id INTEGER NOT NULL REFERENCES service_charge_groups(id),
 property_id INTEGER NOT NULL REFERENCES properties(id), share_percent REAL NOT NULL DEFAULT 0 CHECK(share_percent>=0 AND share_percent<=100),
 PRIMARY KEY(group_id,property_id)
);
ALTER TABLE cost_lines ADD COLUMN service_group_id INTEGER REFERENCES service_charge_groups(id);
ALTER TABLE cost_lines ADD COLUMN allocation_method TEXT NOT NULL DEFAULT 'tenancy_days'
 CHECK(allocation_method IN ('tenancy_days','equal','floor_area','fixed_percentage'));
CREATE TABLE published_statements_backup AS SELECT * FROM published_statements;
DROP TABLE published_statements;
CREATE TABLE settlements_rebuild (
 id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id), property_id INTEGER NOT NULL REFERENCES properties(id),
 year INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'draft', snapshot TEXT NOT NULL,
 created_by INTEGER NOT NULL REFERENCES users(id), approved_by INTEGER REFERENCES users(id), created_at TEXT NOT NULL,
 service_group_id INTEGER REFERENCES service_charge_groups(id)
);
INSERT INTO settlements_rebuild(id,company_id,property_id,year,status,snapshot,created_by,approved_by,created_at)
 SELECT id,company_id,property_id,year,status,snapshot,created_by,approved_by,created_at FROM settlements;
DROP TABLE settlements;
ALTER TABLE settlements_rebuild RENAME TO settlements;
CREATE UNIQUE INDEX settlement_property_year ON settlements(property_id,year) WHERE service_group_id IS NULL;
CREATE UNIQUE INDEX settlement_group_year ON settlements(service_group_id,year) WHERE service_group_id IS NOT NULL;
CREATE TABLE published_statements (
 id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id), settlement_id INTEGER NOT NULL REFERENCES settlements(id),
 contract_id INTEGER NOT NULL REFERENCES contracts(id), document_id INTEGER NOT NULL REFERENCES documents(id),
 published_by INTEGER NOT NULL REFERENCES users(id), published_at TEXT NOT NULL,
 UNIQUE(settlement_id,contract_id)
);
INSERT INTO published_statements SELECT * FROM published_statements_backup;
DROP TABLE published_statements_backup;
CREATE INDEX service_charge_group_company ON service_charge_groups(company_id,id);
CREATE INDEX service_charge_unit_property ON service_charge_group_units(property_id,group_id);
'''

V11 = '''
CREATE TABLE cost_lines_rebuild (
 id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id), property_id INTEGER NOT NULL REFERENCES properties(id),
 year INTEGER NOT NULL, category TEXT NOT NULL, supplier TEXT NOT NULL, amount_cents INTEGER NOT NULL CHECK(amount_cents>0),
 document_id INTEGER REFERENCES documents(id), created_by INTEGER NOT NULL REFERENCES users(id), created_at TEXT NOT NULL,
 service_group_id INTEGER REFERENCES service_charge_groups(id),
 allocation_method TEXT NOT NULL DEFAULT 'tenancy_days' CHECK(allocation_method IN ('tenancy_days','equal','floor_area','fixed_percentage')),
 entry_method TEXT NOT NULL DEFAULT 'invoice' CHECK(entry_method IN ('invoice','manual','import'))
);
INSERT INTO cost_lines_rebuild(id,company_id,property_id,year,category,supplier,amount_cents,document_id,created_by,created_at,service_group_id,allocation_method,entry_method)
 SELECT id,company_id,property_id,year,category,supplier,amount_cents,document_id,created_by,created_at,service_group_id,allocation_method,'invoice' FROM cost_lines;
DROP TABLE cost_lines;
ALTER TABLE cost_lines_rebuild RENAME TO cost_lines;
'''

V12 = '''
CREATE TABLE contract_signature_requests (
 id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id), contract_id INTEGER NOT NULL REFERENCES contracts(id),
 source_document_id INTEGER NOT NULL REFERENCES documents(id), completed_document_id INTEGER REFERENCES documents(id),
 envelope_id TEXT NOT NULL UNIQUE, status TEXT NOT NULL, tenant_email TEXT NOT NULL, landlord_email TEXT NOT NULL,
 created_by INTEGER NOT NULL REFERENCES users(id), created_at TEXT NOT NULL, updated_at TEXT NOT NULL, completed_at TEXT
);
CREATE INDEX signature_requests_contract ON contract_signature_requests(company_id,contract_id,id);
'''

V13 = '''
DROP TRIGGER IF EXISTS contract_identity_locked;
CREATE TRIGGER contract_identity_locked BEFORE UPDATE OF signed_on,company_id,property_id,tenant_id,start_date ON contracts
WHEN NOT (OLD.status='pending signature' AND NEW.status='active' AND NEW.company_id=OLD.company_id AND NEW.property_id=OLD.property_id AND NEW.tenant_id=OLD.tenant_id AND NEW.start_date=OLD.start_date AND NEW.end_date IS OLD.end_date)
BEGIN SELECT RAISE(ABORT,'Contract identity is locked'); END;
'''

V14 = '''
ALTER TABLE tenants ADD COLUMN archived_at TEXT;
'''


V15 = '''
CREATE TABLE owners (
 id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id), name TEXT NOT NULL,
 email TEXT NOT NULL, phone TEXT NOT NULL DEFAULT '', payout_details TEXT NOT NULL DEFAULT '',
 archived_at TEXT, created_at TEXT NOT NULL
);
CREATE TABLE portfolios (
 id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id), owner_id INTEGER NOT NULL REFERENCES owners(id),
 name TEXT NOT NULL, fee_percent REAL NOT NULL DEFAULT 0 CHECK(fee_percent>=0 AND fee_percent<=100), created_at TEXT NOT NULL,
 UNIQUE(company_id,name)
);
ALTER TABLE properties ADD COLUMN portfolio_id INTEGER REFERENCES portfolios(id);
CREATE INDEX properties_portfolio ON properties(portfolio_id);
CREATE TABLE owner_accounts (
 user_id INTEGER NOT NULL REFERENCES users(id), owner_id INTEGER NOT NULL UNIQUE REFERENCES owners(id),
 company_id INTEGER NOT NULL REFERENCES companies(id), enabled INTEGER NOT NULL DEFAULT 1,
 created_at TEXT NOT NULL, PRIMARY KEY(user_id,owner_id)
);
CREATE TABLE owner_expenses (
 id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id), portfolio_id INTEGER NOT NULL REFERENCES portfolios(id),
 property_id INTEGER REFERENCES properties(id), incurred_on TEXT NOT NULL, category TEXT NOT NULL, description TEXT NOT NULL,
 amount_cents INTEGER NOT NULL CHECK(amount_cents>0), document_id INTEGER REFERENCES documents(id),
 created_by INTEGER NOT NULL REFERENCES users(id), created_at TEXT NOT NULL
);
CREATE TABLE owner_statements (
 id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id), portfolio_id INTEGER NOT NULL REFERENCES portfolios(id),
 period TEXT NOT NULL, collected_cents INTEGER NOT NULL, fee_cents INTEGER NOT NULL, expenses_cents INTEGER NOT NULL,
 net_cents INTEGER NOT NULL, snapshot TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','published','paid')),
 created_by INTEGER NOT NULL REFERENCES users(id), published_at TEXT, paid_on TEXT, payout_reference TEXT,
 paid_by INTEGER REFERENCES users(id), created_at TEXT NOT NULL, UNIQUE(portfolio_id,period)
);
CREATE TRIGGER owner_statement_frozen BEFORE UPDATE OF collected_cents,fee_cents,expenses_cents,net_cents,snapshot,period,portfolio_id ON owner_statements BEGIN SELECT RAISE(ABORT,'Owner statement figures are frozen'); END;
ALTER TABLE account_invitations RENAME TO account_invitations_v14;
CREATE TABLE account_invitations (
 id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id), email TEXT NOT NULL,
 name TEXT NOT NULL, kind TEXT NOT NULL CHECK(kind IN ('tenant','contractor','owner')),
 target_id INTEGER NOT NULL, token_hash TEXT NOT NULL UNIQUE, expires_at TEXT NOT NULL,
 used_at TEXT, revoked INTEGER NOT NULL DEFAULT 0, created_by INTEGER NOT NULL REFERENCES users(id), created_at TEXT NOT NULL
);
INSERT INTO account_invitations SELECT id,company_id,email,name,kind,target_id,token_hash,expires_at,used_at,revoked,created_by,created_at FROM account_invitations_v14;
DROP TABLE account_invitations_v14;
'''

V16 = '''
CREATE TABLE sla_policies (
 company_id INTEGER NOT NULL REFERENCES companies(id), urgency TEXT NOT NULL CHECK(urgency IN ('Emergency','Urgent','Routine')),
 response_hours INTEGER NOT NULL CHECK(response_hours BETWEEN 1 AND 720), resolve_days INTEGER NOT NULL CHECK(resolve_days BETWEEN 1 AND 90),
 PRIMARY KEY(company_id,urgency)
);
ALTER TABLE repairs ADD COLUMN response_due_at TEXT;
ALTER TABLE repairs ADD COLUMN responded_at TEXT;
CREATE TABLE automation_rules (
 id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id), name TEXT NOT NULL,
 trigger TEXT NOT NULL CHECK(trigger IN ('invoice_overdue','invoice_due_soon','contract_ending','repair_open','proof_pending')),
 days INTEGER NOT NULL CHECK(days BETWEEN 0 AND 365),
 action TEXT NOT NULL CHECK(action IN ('create_task','notify_staff','notify_tenant')),
 title TEXT NOT NULL, body TEXT NOT NULL DEFAULT '', owner TEXT NOT NULL DEFAULT 'Property team',
 enabled INTEGER NOT NULL DEFAULT 1, created_by INTEGER NOT NULL REFERENCES users(id), created_at TEXT NOT NULL
);
CREATE TABLE automation_runs (
 id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id), rule_id INTEGER NOT NULL REFERENCES automation_rules(id) ON DELETE CASCADE,
 entity TEXT NOT NULL, entity_id INTEGER NOT NULL, summary TEXT NOT NULL, ran_at TEXT NOT NULL, UNIQUE(rule_id,entity,entity_id)
);
CREATE TABLE custom_fields (
 id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id),
 entity TEXT NOT NULL CHECK(entity IN ('tenant','property','contract','owner')), key TEXT NOT NULL, label TEXT NOT NULL,
 field_type TEXT NOT NULL DEFAULT 'text' CHECK(field_type IN ('text','number','date')), created_at TEXT NOT NULL, UNIQUE(company_id,entity,key)
);
CREATE TABLE custom_values (
 company_id INTEGER NOT NULL REFERENCES companies(id), field_id INTEGER NOT NULL REFERENCES custom_fields(id) ON DELETE CASCADE,
 entity_id INTEGER NOT NULL, value TEXT NOT NULL, PRIMARY KEY(field_id,entity_id)
);
CREATE TABLE doc_templates (
 id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id), name TEXT NOT NULL,
 kind TEXT NOT NULL CHECK(kind IN ('Lease','Letter','Notice','Other')), body TEXT NOT NULL,
 archived INTEGER NOT NULL DEFAULT 0, created_by INTEGER NOT NULL REFERENCES users(id), created_at TEXT NOT NULL, UNIQUE(company_id,name)
);
CREATE TABLE owner_documents (
 id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id), owner_id INTEGER NOT NULL REFERENCES owners(id),
 document_id INTEGER NOT NULL REFERENCES documents(id), title TEXT NOT NULL, category TEXT NOT NULL,
 shared_by INTEGER NOT NULL REFERENCES users(id), from_owner INTEGER NOT NULL DEFAULT 0, shared_at TEXT NOT NULL,
 revoked INTEGER NOT NULL DEFAULT 0, UNIQUE(owner_id,document_id)
);
'''

V17 = '''
CREATE TABLE thread_states (
 thread_id INTEGER PRIMARY KEY REFERENCES portal_messages(id), company_id INTEGER NOT NULL REFERENCES companies(id),
 status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','resolved')), assignee_id INTEGER REFERENCES users(id),
 updated_by INTEGER REFERENCES users(id), updated_at TEXT NOT NULL
);
CREATE TABLE canned_replies (
 id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id), title TEXT NOT NULL, body TEXT NOT NULL,
 created_by INTEGER NOT NULL REFERENCES users(id), created_at TEXT NOT NULL, UNIQUE(company_id,title)
);
'''

V18 = '''
CREATE TABLE lease_offers (
 id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id), property_id INTEGER NOT NULL REFERENCES properties(id),
 tenant_id INTEGER NOT NULL REFERENCES tenants(id), template_id INTEGER REFERENCES doc_templates(id),
 start_date TEXT NOT NULL, end_date TEXT, basic_cents INTEGER NOT NULL, service_cents INTEGER NOT NULL DEFAULT 0, deposit_cents INTEGER NOT NULL DEFAULT 0,
 body TEXT NOT NULL, document_id INTEGER REFERENCES documents(id),
 status TEXT NOT NULL DEFAULT 'sent' CHECK(status IN ('sent','signed','returned','approved','declined','withdrawn')),
 sent_by INTEGER REFERENCES users(id), sent_at TEXT NOT NULL,
 signer_name TEXT, signed_at TEXT, signed_ip TEXT, signed_hash TEXT,
 response_note TEXT, reviewed_by INTEGER REFERENCES users(id), reviewed_at TEXT,
 contract_id INTEGER REFERENCES contracts(id), final_document_id INTEGER REFERENCES documents(id)
);
CREATE INDEX lease_offers_company ON lease_offers(company_id,status);
'''

V19 = '''
CREATE TABLE company_logos (
 company_id INTEGER PRIMARY KEY REFERENCES companies(id), mime TEXT NOT NULL, data BLOB NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE invoice_revisions (
 id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id), invoice_id INTEGER NOT NULL REFERENCES invoices(id),
 revision INTEGER NOT NULL, before_json TEXT NOT NULL, after_json TEXT NOT NULL, reason TEXT NOT NULL,
 edited_by INTEGER NOT NULL REFERENCES users(id), created_at TEXT NOT NULL, UNIQUE(invoice_id,revision)
);
CREATE TRIGGER invoice_revision_no_update BEFORE UPDATE ON invoice_revisions BEGIN SELECT RAISE(ABORT,'Invoice revisions are immutable'); END;
CREATE TRIGGER invoice_revision_no_delete BEFORE DELETE ON invoice_revisions BEGIN SELECT RAISE(ABORT,'Invoice revisions are immutable'); END;
DROP TRIGGER invoice_no_update;
CREATE TRIGGER invoice_no_update BEFORE UPDATE ON invoices
WHEN NEW.id IS NOT OLD.id OR NEW.company_id IS NOT OLD.company_id OR NEW.contract_id IS NOT OLD.contract_id OR NEW.number IS NOT OLD.number OR NEW.period IS NOT OLD.period OR NEW.created_at IS NOT OLD.created_at
BEGIN SELECT RAISE(ABORT,'Issued invoices are immutable: only amounts, due date and lines can be revised, and every revision is kept'); END;
'''

V20 = '''
ALTER TABLE lease_offers ADD COLUMN form_fields TEXT;
ALTER TABLE lease_offers ADD COLUMN form_values TEXT;
ALTER TABLE lease_offers ADD COLUMN tenant_signature BLOB;
ALTER TABLE lease_offers ADD COLUMN tenant_signature_mode TEXT;
ALTER TABLE lease_offers ADD COLUMN landlord_signature BLOB;
ALTER TABLE lease_offers ADD COLUMN landlord_signature_mode TEXT;
ALTER TABLE lease_offers ADD COLUMN edited INTEGER NOT NULL DEFAULT 0;
'''


V21 = '''
CREATE TABLE company_signatures (
 company_id INTEGER PRIMARY KEY REFERENCES companies(id), signer_name TEXT NOT NULL, signer_title TEXT NOT NULL DEFAULT '',
 mode TEXT NOT NULL, data BLOB NOT NULL, updated_at TEXT NOT NULL
);
ALTER TABLE lease_offers ADD COLUMN landlord_signer_name TEXT;
ALTER TABLE lease_offers ADD COLUMN landlord_signer_title TEXT;
ALTER TABLE lease_offers ADD COLUMN landlord_signed_at TEXT;
'''


V22 = '''
ALTER TABLE companies ADD COLUMN review_invoices INTEGER NOT NULL DEFAULT 0;
ALTER TABLE invoices ADD COLUMN released_at TEXT;
UPDATE invoices SET released_at=created_at;
'''


def migrate(conn):
    conn.execute('CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)')
    # Execute each statement (including multi-line triggers) inside one transaction.
    import sqlite3
    from .db import now
    conn.execute('BEGIN IMMEDIATE')
    try:
        if not conn.execute('SELECT 1 FROM schema_migrations WHERE version=2').fetchone():
            statement = ''
            for line in V2.splitlines():
                statement += line + '\n'
                if sqlite3.complete_statement(statement):
                    conn.execute(statement)
                    statement = ''
            conn.execute('INSERT INTO schema_migrations VALUES(?,?)', (2, now()))
        if not conn.execute('SELECT 1 FROM schema_migrations WHERE version=3').fetchone():
            statement = ''
            for line in V3.splitlines():
                statement += line + '\n'
                if sqlite3.complete_statement(statement):
                    conn.execute(statement)
                    statement = ''
            conn.execute('INSERT INTO schema_migrations VALUES(?,?)', (3, now()))
        if not conn.execute('SELECT 1 FROM schema_migrations WHERE version=4').fetchone():
            statement = ''
            for line in V4.splitlines():
                statement += line + '\n'
                if sqlite3.complete_statement(statement):
                    conn.execute(statement)
                    statement = ''
            conn.execute('INSERT INTO schema_migrations VALUES(?,?)', (4, now()))
        if not conn.execute('SELECT 1 FROM schema_migrations WHERE version=5').fetchone():
            statement = ''
            for line in V5.splitlines():
                statement += line + '\n'
                if sqlite3.complete_statement(statement):
                    conn.execute(statement)
                    statement = ''
            conn.execute('INSERT INTO schema_migrations VALUES(?,?)', (5, now()))
        if not conn.execute('SELECT 1 FROM schema_migrations WHERE version=6').fetchone():
            statement = ''
            for line in V6.splitlines():
                statement += line + '\n'
                if sqlite3.complete_statement(statement):
                    conn.execute(statement)
                    statement = ''
            conn.execute('INSERT INTO schema_migrations VALUES(?,?)', (6, now()))
        if not conn.execute('SELECT 1 FROM schema_migrations WHERE version=7').fetchone():
            statement = ''
            for line in V7.splitlines():
                statement += line + '\n'
                if sqlite3.complete_statement(statement):
                    conn.execute(statement)
                    statement = ''
            conn.execute('INSERT INTO schema_migrations VALUES(?,?)', (7, now()))
        if not conn.execute('SELECT 1 FROM schema_migrations WHERE version=8').fetchone():
            statement = ''
            for line in V8.splitlines():
                statement += line + '\n'
                if sqlite3.complete_statement(statement):
                    conn.execute(statement)
                    statement = ''
            conn.execute('INSERT INTO schema_migrations VALUES(?,?)', (8, now()))
        if not conn.execute('SELECT 1 FROM schema_migrations WHERE version=9').fetchone():
            statement = ''
            for line in V9.splitlines():
                statement += line + '\n'
                if sqlite3.complete_statement(statement):
                    conn.execute(statement)
                    statement = ''
            conn.execute('INSERT INTO schema_migrations VALUES(?,?)', (9, now()))
        if not conn.execute('SELECT 1 FROM schema_migrations WHERE version=10').fetchone():
            statement = ''
            for line in V10.splitlines():
                statement += line + '\n'
                if sqlite3.complete_statement(statement):
                    conn.execute(statement)
                    statement = ''
            conn.execute('INSERT INTO schema_migrations VALUES(?,?)', (10, now()))
        if not conn.execute('SELECT 1 FROM schema_migrations WHERE version=11').fetchone():
            statement = ''
            for line in V11.splitlines():
                statement += line + '\n'
                if sqlite3.complete_statement(statement):
                    conn.execute(statement)
                    statement = ''
            conn.execute('INSERT INTO schema_migrations VALUES(?,?)', (11, now()))
        if not conn.execute('SELECT 1 FROM schema_migrations WHERE version=12').fetchone():
            statement = ''
            for line in V12.splitlines():
                statement += line + '\n'
                if sqlite3.complete_statement(statement):
                    conn.execute(statement)
                    statement = ''
            conn.execute('INSERT INTO schema_migrations VALUES(?,?)', (12, now()))
        if not conn.execute('SELECT 1 FROM schema_migrations WHERE version=13').fetchone():
            statement = ''
            for line in V13.splitlines():
                statement += line + '\n'
                if sqlite3.complete_statement(statement):
                    conn.execute(statement)
                    statement = ''
            conn.execute('INSERT INTO schema_migrations VALUES(?,?)', (13, now()))
        if not conn.execute('SELECT 1 FROM schema_migrations WHERE version=14').fetchone():
            statement = ''
            for line in V14.splitlines():
                statement += line + '\n'
                if sqlite3.complete_statement(statement):
                    conn.execute(statement)
                    statement = ''
            conn.execute('INSERT INTO schema_migrations VALUES(?,?)', (14, now()))
        if not conn.execute('SELECT 1 FROM schema_migrations WHERE version=15').fetchone():
            statement = ''
            for line in V15.splitlines():
                statement += line + '\n'
                if sqlite3.complete_statement(statement):
                    conn.execute(statement)
                    statement = ''
            conn.execute('INSERT INTO schema_migrations VALUES(?,?)', (15, now()))
        if not conn.execute('SELECT 1 FROM schema_migrations WHERE version=16').fetchone():
            statement = ''
            for line in V16.splitlines():
                statement += line + chr(10)
                if sqlite3.complete_statement(statement):
                    conn.execute(statement)
                    statement = ''
            conn.execute('INSERT INTO schema_migrations VALUES(?,?)', (16, now()))
        if not conn.execute('SELECT 1 FROM schema_migrations WHERE version=17').fetchone():
            statement = ''
            for line in V17.splitlines():
                statement += line + chr(10)
                if sqlite3.complete_statement(statement):
                    conn.execute(statement)
                    statement = ''
            conn.execute('INSERT INTO schema_migrations VALUES(?,?)', (17, now()))
        if not conn.execute('SELECT 1 FROM schema_migrations WHERE version=18').fetchone():
            statement = ''
            for line in V18.splitlines():
                statement += line + chr(10)
                if sqlite3.complete_statement(statement):
                    conn.execute(statement)
                    statement = ''
            conn.execute('INSERT INTO schema_migrations VALUES(?,?)', (18, now()))
        if not conn.execute('SELECT 1 FROM schema_migrations WHERE version=19').fetchone():
            statement = ''
            for line in V19.splitlines():
                statement += line + chr(10)
                if sqlite3.complete_statement(statement):
                    conn.execute(statement)
                    statement = ''
            conn.execute('INSERT INTO schema_migrations VALUES(?,?)', (19, now()))
        if not conn.execute('SELECT 1 FROM schema_migrations WHERE version=20').fetchone():
            statement = ''
            for line in V20.splitlines():
                statement += line + chr(10)
                if sqlite3.complete_statement(statement):
                    conn.execute(statement)
                    statement = ''
            conn.execute('INSERT INTO schema_migrations VALUES(?,?)', (20, now()))
        if not conn.execute('SELECT 1 FROM schema_migrations WHERE version=21').fetchone():
            statement = ''
            for line in V21.splitlines():
                statement += line + chr(10)
                if sqlite3.complete_statement(statement):
                    conn.execute(statement)
                    statement = ''
            conn.execute('INSERT INTO schema_migrations VALUES(?,?)', (21, now()))
        if not conn.execute('SELECT 1 FROM schema_migrations WHERE version=22').fetchone():
            statement = ''
            for line in V22.splitlines():
                statement += line + chr(10)
                if sqlite3.complete_statement(statement):
                    conn.execute(statement)
                    statement = ''
            conn.execute('INSERT INTO schema_migrations VALUES(?,?)', (22, now()))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
