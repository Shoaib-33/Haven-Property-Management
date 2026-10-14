# Haven property management

This implementation adapts the supplied Dutch property management blueprint to manual payment verification. There are no bank connections, payment gateways, direct debits, or automatic bank matching.

## Workflow

Staff create companies, properties, tenants and dated contracts. A monthly run issues invoices. Each invoice has a revocable, expiring submission link. A tenant pays outside the application and uploads a receipt. Finance verifies actual receipt independently, then approves or rejects the submission. Approval creates an immutable payment posting and updates the calculated invoice balance. An upload alone never records payment.

## Application scope

- Staff authentication, roles assigned per company, tenant submission links.
- Portfolio, parties, contracts and versioned rent history.
- Stored, manually verified WWS valuations; no home-grown legal points engine.
- Monthly invoices, printable invoice documents, partial payments and arrears.
- Payment evidence uploads, duplicate detection, finance review and reversals.
- Deposit receipt, evidence-backed deductions with second-person approval, refund records.
- Service-charge cost evidence, reviewed allocation and statement generation.
- Inspection findings, photos, completion, and historical snapshots.
- Repairs, status history, completion evidence and deadline tasks.
- Communication timeline and document vault.
- Proposed rent changes with effective dates and separate approval.
- Assigned tasks, reference-rule records and audit events.
- Summary reports and data exports.

## Added tenant and contractor portals

Tenants can activate an account by staff invitation and see invoices and balances belonging to linked contracts, submit payment evidence, download documents expressly shared by staff, report repairs and follow updates, send private messages and submit a complaint, payment-plan request or proposed notice date. A tenant end-date request does not itself end the tenancy. Password changes revoke sessions. Staff can pause a tenant portal account.

Contractors activate through invitation and only see work orders assigned to their contractor record. They receive the minimum on-site contact information, submit appointment updates, propose a quote, and upload work evidence or an invoice. Quotes need a second-person decision. A contractor cannot close a repair. Staff inspect the completion evidence and close the order.

## Production boundaries

This local application uses SQLite and filesystem attachments. Database-enforced PostgreSQL row-level security, external daily audit anchors, automatic email/WhatsApp delivery, qualified signatures, offline inspection synchronization, durable field-staff assignment, a pinned external WWS engine and Twinfield export certification are separate deployment/integration work. Outbound notices and invitations are not emailed; authorized staff copy/share activation links and record communications.

Legal thresholds in the blueprint are unverified. Reference rules and WWS valuations require a source and human verification. The application must not claim that a stored figure establishes legal compliance. A Dutch tenancy specialist must validate rules and tenant letter templates before operational use.

Demo data is fictional. Do not store real tenant records on an unsecured development server. Read README.md for startup, credentials and testing.
