# Staff and tenant system expansion

The bank-free payment process remains unchanged: submission, independent verification, approval, immutable posting.

## Delivery checklist

- [ ] Add versioned, non-destructive database upgrades.
- [ ] Tenant invitations, account activation, login, password changes and access revocation.
- [ ] Tenant dashboard, invoices, payment proof, deposit history and shared documents.
- [ ] Tenant repair reporting with attachments, progress and appointments.
- [ ] Private tenant/staff messages, requests, complaints and notices.
- [ ] Contractor accounts, assigned jobs, quotes and completion evidence.
- [ ] Staff work-order management and verified closure.
- [ ] Reviewed invoice credits and payment plans.
- [ ] Tenant-specific annual statement PDFs and in-portal publication.
- [ ] Company/profile editing and role-aware controls.
- [ ] Notifications and repeat-safe maintenance/deadline jobs.
- [ ] Server-side isolation tests and browser tests for all roles.
- [ ] Upgrade existing fictional data and document startup and accounts.

## Scope boundaries requiring external decisions

Hosting, live credentials, Dutch lawyer-approved templates and legal calculation inputs remain external dependencies. Provider connections will not be invented. The local product will expose its implemented workflows honestly and preserve existing records during upgrades. The blueprint's automatic WWS engine, PostgreSQL RLS, external delivery proofs and certified accounting integration remain separate production requirements unless actually implemented and verified.
