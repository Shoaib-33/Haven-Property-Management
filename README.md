---
title: Haven property management demo
emoji: 🏠
colorFrom: green
colorTo: gray
sdk: docker
app_port: 7860
---

# Haven property management

A local property-management application adapted from the supplied Dutch blueprint. Payments are made outside the application. Tenants submit receipts through expiring links and finance verifies actual receipt before updating balances.

## Run on Windows

Open PowerShell in `E:\Dutch`:

```powershell
python -m pip install -r requirements.txt
python manage.py demo
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open **http://127.0.0.1:8000**. The terminal must stay open. Press **Ctrl+C** to stop.

The dependencies are already available in the Python installation used to build this workspace. The install command is for another environment or a missing dependency.

Alternatively, after installing dependencies, run `powershell -ExecutionPolicy Bypass -File .\start.ps1`.

## Automatic monthly bills

When the server is running, Haven automatically creates the next month's invoice for each active tenancy on the 15th of the month and adds an in-app bill alert for tenants with an activated portal account. When SMTP is configured, Haven also emails each newly issued invoice to the tenant as a PDF attachment. It retries during the rest of the month without creating duplicate invoices. If the server was unavailable around the run date, it catches up for the current month during the first three days. The invoice is due on the first day of its billing month and appears in the **Rent & payments** page with a PDF download.

Set HAVEN_RENT_BILL_DAY to a day from 1 to 28 to change the monthly run date. The default is 15. Bills appear in the tenant portal. Overdue bills create an in-portal alert and staff follow-up task; optional SMTP can also send friendly overdue emails on day 1 and day 8. These are not statutory notices. Bank collection and payment matching are not configured; tenants still pay externally and upload proof for staff verification.

#### SMTP invoice and arrears email

For local use, copy `.env.example` to `.env` in the project folder and fill in your SMTP provider's values. Haven loads `.env` automatically when it starts; values already set by the operating system or deployment take precedence. Keep `.env` private and out of source control. On Hugging Face, set the same names in Space Variables and Secrets. SMTP needs `HAVEN_SMTP_HOST`, `HAVEN_SMTP_FROM`, and `HAVEN_PUBLIC_URL`; set `HAVEN_SMTP_PORT` (default `587`), `HAVEN_SMTP_USERNAME`, `HAVEN_SMTP_PASSWORD`, and optionally `HAVEN_SMTP_STARTTLS` (default `1`) or `HAVEN_SMTP_SSL` (default `0`) for your provider. If SMTP is not configured, Haven continues using in-portal invoices and alerts and staff tasks. Failed deliveries retry automatically up to five times; finance staff can retry failed invoice or reminder emails from the relevant page. Invoice messages include the PDF invoice; overdue messages are friendly reminders only. Haven does not automatically send a formal 14-day notice or postal letters.

### Demo accounts

| Email | Role | Password |
|---|---|---|
| `admin@haven.local` | Administrator; both companies | `HavenDemo!2026` |
| `finance@haven.local` | Finance; Haven Residential only | `HavenDemo!2026` |
| `manager@haven.local` | Manager; Haven Residential only | `HavenDemo!2026` |
| `tenant@haven.local` | Fictional tenant; only Emma's tenancy | `HavenDemo!2026` |
| `contractor@haven.local` | Fictional contractor; one assigned repair only | `HavenDemo!2026` |

All sample names, receipts and financial entries are fictional. `demo` initializes only an empty database and never resets existing records. These credentials are for a loopback-only demonstration, not a public deployment.

### Hugging Face demo Space

The root `Dockerfile` and Space metadata support a Docker Space on port 7860. This deployment starts an empty workspace and creates only the initial administrator and company; it does not import the local database or run `manage.py demo`. Set `HAVEN_BOOTSTRAP_ADMIN_EMAIL` as a Space variable and `HAVEN_BOOTSTRAP_ADMIN_PASSWORD` as a Space secret (12 or more characters). Optional `HAVEN_BOOTSTRAP_ADMIN_NAME` and `HAVEN_BOOTSTRAP_COMPANY_NAME` variables set their display names. The database and uploads are stored under `/data`; attach persistent storage at `/data` if records must survive Space restarts. Without persistent storage, treat the Space as disposable demo data.

The staff portal is `/` and the tenant portal is `/tenant` on the same Space URL. After signing in as the administrator, create a tenant and activate their tenant portal account from the staff workspace.

To enable arrears email on the Space, add `HAVEN_SMTP_HOST`, `HAVEN_SMTP_PORT`, `HAVEN_SMTP_FROM`, and `HAVEN_PUBLIC_URL` as variables, and add `HAVEN_SMTP_USERNAME` / `HAVEN_SMTP_PASSWORD` as secrets if your provider requires authentication. Use the Space's HTTPS URL for `HAVEN_PUBLIC_URL`. Set either `HAVEN_SMTP_STARTTLS=1` (default) or `HAVEN_SMTP_SSL=1` according to the provider. Email remains disabled until the required host, sender, and public URL are set.

Tenant portal: **http://127.0.0.1:8000/tenant**. Contractor portal: **http://127.0.0.1:8000/contractor**. The first `demo` startup initializes sample portal accounts. On an existing fictional Haven demo database, running `python manage.py demo` once adds the sample accounts and one fictional assigned repair; it does not reset existing data. For real tenants, staff use **Settings → Tenant access → Create link** and send the one-use, 7-day activation link through their existing secure communication channel.

Invitation-generated URLs for tenants and contractors are for account activation. Ask each account holder to bookmark the matching `/tenant` or `/contractor` sign-in page after activation. Tenant and contractor passwords can be changed from their portal.

## Try the payment workflow

1. Sign in as the administrator. Open **Invoices → Manage → Create tenant link**.
2. Open the generated link in another tab. The link requires no staff account.
3. Submit a PDF, PNG or JPEG receipt, amount, payment date and reference.
4. Return to the staff application and open **Payment verification**. Refresh the browser if the new receipt is not visible.
5. Review the proof. Confirm that the funds actually arrived, add a review note, then approve.
6. The invoice balance updates; a partial payment leaves an outstanding balance.
7. Managers may reverse an approved payment with a reason. The original posting remains in history.

An internal staff member cannot approve their own submission. Use the manager account to review a receipt recorded by the finance account. A receipt submitted using the tenant link can be reviewed by any authorized finance reviewer.

Localhost links work only on this computer. Sharing usable links with real tenants requires HTTPS hosting, access configuration and production security work.

## Staff and tenant workflows

Staff can invite a tenant using **Settings → Tenant access**. On activation, the tenant gets a private portal with their invoices, current balances, shared documents, repair requests, updates, messages, complaints, payment questions and written move-out requests. Each account sees only its explicitly linked tenancy. Tenants upload receipts themselves; finance still confirms receipt before a payment posts. Sharing a tenancy document with a portal account is an explicit staff action.

Staff can register contractors in **Contractors** and attach a signed data-processing agreement. An assigned work order gives the contractor only that job, its location, the relevant tenant contact and the work scope. Contractors can schedule visits, send quotes and upload completion evidence or invoices. A manager reviews quotes and completion evidence; contractor uploads never close a repair by themselves.

### Portfolio import, manual contract signing and rent indexation

In **Tenants & contracts**, use **Import portfolio CSV** to download the example template, preview up to 1,000 rows, then confirm the all-or-nothing import. Rows can add properties, tenants, or tenancies with their rent and deposit. Imports match existing property names and tenant email addresses and do not overwrite existing records. Use ISO dates (`YYYY-MM-DD`) and keep one tenant/property tenancy per row.

To record a manually signed agreement, have the landlord and tenant sign the PDF outside Haven, then upload the completed copy under **Documents** with category **Contract**. Choose **Record signed contract** under **Tenants & contracts**, enter the tenancy and rent details, select the signed PDF, and confirm that both parties have signed. Haven activates the tenancy and shares the signed PDF in that tenant's Documents portal. The confirmation records the landlord's attestation; it does not verify signatures or provide legal e-signing.

On **Rent & valuations**, **Calculate indexation** applies the landlord-entered percentage to the current basic rent and creates a dated rent proposal. A second authorized person must review it; approval records a new rent version from the effective date. Service-charge advance is unchanged. Haven does not decide whether a percentage or notice complies with current rent rules, so verify the applicable requirements before proposing it.

## Modules

Portfolio and companies; tenants and contracts; dated rent records; invoice runs and PDFs; payment evidence and review; arrears follow-up tasks; deposit review; service-charge costs and unit-based annual drafts; stored external WWS reports and rent-change proposals; maintenance; inspections; communication notes; document vault; tasks; ledger CSV; tenancy summaries; audit history; team access.

The interface is English, with bilingual invoice labels. Full Dutch interface localization is not implemented. Portal messages stay inside the application, while email, WhatsApp and physical notices are not sent. The project deliberately has no bank connection, payment gateway or direct-debit integration.

## Storage and architecture

- Backend: Python / FastAPI.
- Frontend: responsive HTML, CSS and JavaScript; no frontend build or Node dependency needed to run.
- Local database: SQLite, foreign keys, serialized financial writes, immutable invoice/ledger/audit triggers.
- Attachments: `data/uploads`, served only through authenticated endpoints.
- Access: per-company server checks, role checks, hashed passwords, server-side sessions, CSRF protection.
- Files accepted: PDF/PNG/JPEG by header, maximum 10 MB. Attachments download rather than execute inline.
- Money: integer cents; input rejects excess decimals; prorating uses decimal rounding.

## Tests

```powershell
python -m unittest discover -s tests -v
```

Tests use isolated temporary databases. They do not change demo data.

## Clean non-demo workspace

Set a separate data directory and create your administrator. No default password is created:

```powershell
$env:HAVEN_DATA_DIR = 'E:\Dutch\data-clean'
python manage.py create-admin
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Sign in and create the first company. Use Settings to add staff. Keep the environment variable set whenever starting that workspace. This is still a development deployment.

## Backup

Stop writes while backing up attachments and run:

```powershell
python manage.py backup
```

The ZIP includes a consistent SQLite snapshot and uploaded files. Protect backups as personal data. Restore into a separate directory, then select it using `HAVEN_DATA_DIR`.

## Deployment and specification limits

This is an expanded local implementation, **not a certified production deployment of the full Dutch blueprint**. See [docs/SCOPE.md](docs/SCOPE.md) and [docs/IMPLEMENTATION.md](docs/IMPLEMENTATION.md). Existing records receive additive SQLite migrations. Production work remains for PostgreSQL row-level security, automated backup and external audit anchors, retention/privacy automation, upload malware scanning, multi-factor authentication and recovery, encrypted sensitive fields, Dutch localization, the external WWS engine, lawyer-verified legal tables and templates, meter/floor-area service-charge allocation, full move-out/deposit lifecycle, remote HTTPS hosting, email/WhatsApp/delivery providers, qualified e-signatures, Twinfield/Basecone accounting and offline inspection synchronization. Inspector access is property-assignment scoped in this local build.

Legal rules are not certified. Stored reference rules are informational, not an executable legal engine. Service-charge settlement currently allocates costs to individual units by occupancy days. It produces tenant-specific downloadable statements but does not implement all Dutch statutory allocation methods or independently verify costs. Deposit intervals and repair targets need legal review. Tenancy exports are summaries and document indexes, not merged and redacted tribunal bundles. The application must not be represented as automatically ensuring legal compliance.

For an HTTPS deployment, configure `HAVEN_SECURE_COOKIES=1`, a trusted reverse proxy and separate runtime secrets. Do not expose the demo server publicly.

## Owners, portfolios and statements
- **Owners & portfolios** (staff): group units into portfolios, set a management fee, invite the owner to `/owner`.
- **Owner statements** (finance/manager): generate a month from verified ledger payments, subtract fee and recorded expenses, a second person approves, then record the payout by hand. No bank connection exists anywhere in Haven.
- **Landing page** at `/welcome` (Dutch/English). **Installable app**: manifest + service worker (API calls are never cached).
- Demo owner login: `owner@haven.local` / `HavenDemo!2026` (run `python manage.py demo`).

## Deadlines, automation, templates and owner documents
- **Repair deadlines:** each urgency has a first-response time and a resolution time (Maintenance > Edit deadlines). The Maintenance page ranks open repairs by breach risk and the overview raises an alert.
- **Automation** (admin/manager): "when an invoice is N days late, then create a task / notify". Rules only create tasks and in-app notices, act once per record, and can be previewed before running. They also run in the background scheduler.
- **Templates & fields:** letters and leases with `{{tenant.name}}` style merge fields plus custom fields per tenant, property, contract or owner. Missing data blocks generation; PDFs can be downloaded or saved to the vault and shared with the tenant.
- **Owner documents:** share insurance papers, deeds and valuations with owners; owners can upload too. Financial evidence stays private.

## Tickets (tenant inbox)
- **Staff:** a two-pane helpdesk at *Tenant inbox*: filters (Open, Needs reply, Requests, Resolved), search, chat view, tenant context (rent, balance, open repairs), assignment, resolve/reopen, saved quick replies with `{tenant}` and `{property}`, attachments and `j`/`k` navigation. Requests are answered from the same composer.
- **Tenants:** conversations show status chips (Waiting for a reply, Answered, Resolved), an inline reply box, quick-start topics and live updates. A tenant reply reopens a resolved conversation.
- **Tickets:** every message, request, complaint, payment-plan request, move-out notice and repair appears as one ticket (`MSG-`, `REQ-`, `REP-` numbers) with type filters, status, assignee, SLA badges and internal notes (repairs). Staff handle repairs from the same screen: change status, assign a contractor, complete with evidence.

## Overview analytics
Interactive SVG charts on the overview: rent billed vs collected (6/12 months, click a month to focus the page, legend toggles), occupancy donut, overdue rent by age, a six-month rent forecast and ticket health. Headline numbers count up on arrival. Finance charts are hidden from roles without finance access.

## Tenant messages
Two-pane chat for tenants: searchable conversation list with unread/answered/solved chips, date separators and a "New" divider, Enter to send with instant (optimistic) bubbles, suggested replies, attachments, drafts that survive refreshes, "Mark as solved / Reopen" (staff are notified), live toasts when your manager replies, and a list/chat switch on phones.

## Help assistant (chat bubble)

Every signed-in screen (landlord, tenant, owner, contractor) has a **Help** bubble in the bottom-right corner. It explains where things are in Haven and offers buttons that open the right page.

- **With Groq (tried first):** put a free key from https://console.groq.com/keys in `.env` as `GROQ_API_KEY=...` and restart. The model defaults to `openai/gpt-oss-20b`; override it with `HAVEN_GROQ_MODEL`.
- **With Gemini (fallback):** put a free key from https://aistudio.google.com/apikey in `.env` as `GEMINI_API_KEY=...` and restart. The model defaults to `gemini-flash-latest` (Google's alias for the newest Flash model); override it with `HAVEN_GEMINI_MODEL`.
- **Without a key** (or if Gemini is unreachable or over its free quota) a built-in keyword guide answers instead, so the bubble always works.
- **Privacy:** the browser never sees the key. Only the question, the last few chat turns and the current page name are sent to Google. No tenant, invoice or user data is sent, and the assistant cannot read or change anything. Each user is limited to 15 questions a minute.

## Contracts for signature

Instead of recording a contract signed on paper, send it through Haven:

1. **Tenants & contracts > Send contract for signature.** Pick the tenant, property, a *Lease* template (Templates & fields) and the rent, deposit and dates. Haven fills the agreement and freezes it as a PDF. The tenant needs a portal login first.
2. The tenant sees a **Contract ready to sign** card on their home page and under Documents. They open the PDF, type their full name (it must match their record) and tick that they agree. Haven stores the time, IP address and a SHA-256 fingerprint of the text.
3. Staff see **signed contracts waiting for approval**. *Approve* creates the tenancy, the rent history and the move-in tasks, and puts the signed agreement (with both signatures and the fingerprint) in the tenant's Documents. *Return* sends it back with a note, and the tenant signs again. *Withdraw* cancels it, and the tenant can also *Decline*.

A unit cannot have two contracts out for signature in overlapping dates, and approval re-checks that the unit is still free. "Signed on paper? Record it" is still available. This is a simple electronic signature (typed name plus audit trail), not a qualified e-signature.

## Large workspaces (hundreds of tenants)

The staff screens do not download the whole history any more. `GET /api/data` sends a recent window (the last 3 billing months, every invoice with an open balance, and the records they point at) plus small per-month totals for all time. With 500 tenants and 24 months of billing this took the bulk call from about 14.6 MB / 1.2 s to 1.3 MB / 0.26 s.

- **Invoices**, the **tenant ledger** and the **audit trail** are paged and searchable on the server (`/api/invoices`, `/api/ledger`, `/api/audit`: `q`, filters, `page`, `page_size`). Search covers every record, not only the loaded ones.
- Opening an old month on the Overview loads that month on demand (`/api/periods/{YYYY-MM}`). The month-by-month analysis uses the all-time totals.
- Tickets draw 40 conversations at a time ("Show more"); a search shows every match.
- Still sent in full: tenants, contracts, properties, tasks, repairs, messages and requests. These are small per record. If they grow into tens of thousands of rows, page them the same way.

## English and Dutch

A language switch (**EN | NL**) sits at the top of every screen: the landlord workspace, the tenant, owner and contractor portals, and the login pages. The choice is remembered in the browser. The first visit follows the browser language. Switching reloads the page in the chosen language.

- **How it works:** the screens are written in English. `static/i18n-nl.js` maps each English text to Dutch, and `static/i18n.js` applies it to whatever the page draws, including toasts and form errors that come from the server. Dates and amounts switch to Dutch formats (for example `€ 1.200,00` and `14 okt 2026`). Anything without a Dutch entry simply stays English.
- **Editing or adding a translation:** open `static/i18n-nl.js` and add `"English text": "Nederlandse tekst"` under `ui` (screen text) or `api` (server messages). Sentences with a variable part use `{0}`, `{1}` for the variable pieces, for example `"{0} overdue across {1} invoice{2}"`. In the Dutch text, `{1?factuur|facturen}` picks the singular or plural word depending on whether the English suffix was empty or `s`. Keep the English key exactly as it appears on screen.
- **The assistant** answers in Dutch when the screens are in Dutch.
- **Not translated:** text you or your tenants type (names, repair descriptions, messages), invoice and contract PDFs (they follow your template text), and emails. Tenants already have a language field. Sending emails and PDFs in that language is a separate step.

## Demo on Render

For a demo you can show to others, deploy the included Docker setup to [Render](https://render.com):

1. Put the project in a **private** GitHub repository. `.gitignore` already keeps `.env`, `data/` and backups out of it.
2. In Render choose **New > Blueprint**, pick the repository and apply. It reads `render.yaml` (a free Docker web service). Or create a **Web Service** by hand with *Runtime: Docker* and the environment variables below.
3. During the blueprint setup Render asks for `HAVEN_DEMO_PASSWORD` (your own password, 12+ characters, used by every demo login) and optionally `GROQ_API_KEY` (free key from console.groq.com, turns the Help chat into the AI assistant). After the first deploy, copy the `https://….onrender.com` address into `HAVEN_PUBLIC_URL`.
4. Open the address. The demo data (with a logo, a landlord signature and the Dutch contract template) is loaded on every start. Logins (the password you chose in `HAVEN_DEMO_PASSWORD`): `admin@haven.local`, `manager@haven.local`, `tenant@haven.local` (open `/tenant`), `owner@haven.local` (open `/owner`), `contractor@haven.local` (open `/contractor`).

| Variable | Value |
|---|---|
| `HAVEN_DEMO_SEED` | `1` loads the fictional sample data when the service starts |
| `HAVEN_SECURE_COOKIES` | `1` (Render serves HTTPS) |
| `HAVEN_DATA_DIR` | `/data` |
| `HAVEN_TIMEZONE` | the timezone whose date Haven shows, e.g. `Europe/Amsterdam` or `Asia/Dhaka`. Servers run on UTC, so without it the date can be a day behind your own |
| `HAVEN_DEMO_PASSWORD` | your own password for all demo logins (12+ characters). Without it the public default is used, so always set it online |
| `HAVEN_PUBLIC_URL` | your Render address |
| `GROQ_API_KEY` | optional, for the AI Help chat |
| `GEMINI_API_KEY` | optional fallback |

Things to know: on the **free plan** the service sleeps after about 15 minutes without visitors (the next visit takes up to a minute to wake it) and its disk is erased on every restart, so the demo returns to its sample data. Anything entered during a demo is lost. A paid plan with a persistent disk keeps the data. The demo logins are public knowledge, so never put real tenant data in a demo deployment, and share the address only with people you want to have it. To run it with your own administrator instead of sample data, remove `HAVEN_DEMO_SEED` and set `HAVEN_BOOTSTRAP_ADMIN_EMAIL` and `HAVEN_BOOTSTRAP_ADMIN_PASSWORD` (12+ characters).

### Contract form and signatures

When the landlord sends a contract (Tenants & contracts > Send contract for signature):

1. Pick the tenant, property, template, dates and amounts, and tick which details the tenant must fill in (phone, date of birth, ID, address, income, house rules and more, plus two questions of your own).
2. Read the filled-in contract and edit the wording for this tenant if needed. The template itself is not changed.
3. If you saved your signature (Settings > Your signature on contracts), it is already on the contract when it is sent. The tenant reads the contract in their portal, fills in the form and signs by **drawing**, **typing** (three handwriting styles) or **uploading** a photo of their signature.
4. The landlord reviews the answers and the signature, signs too, and approves. The final PDF contains the tenant's details and both signatures, plus the signing time, IP address and a SHA-256 fingerprint.

This is a simple electronic signature, valid for ordinary residential tenancies in the EU. It is built in and free. The signature fonts are bundled in `static/fonts` (SIL Open Font License).

The agreement PDF follows the usual Dutch layout: letterhead with your logo, key facts, numbered articles in Dutch with an English translation under each clause, initials boxes and "Pagina x van y" in the footer, an appendix with the tenant's details, and a signature block. The starter template "Huurovereenkomst woonruimte (NL/EN)" is a starting point: have a legal professional review it before real use.

### Edit before sending

- **Contracts:** step 2 of "Send contract for signature" shows the contract as a document, laid out like the PDF. Click a pencil to change any sentence, add or remove paragraphs, and fill the yellow boxes for details Haven does not know yet. Only then is it sent.
- **Invoices:** every invoice can be edited in place (Invoices > Manage). Turn on Settings > "Review invoices before sending" and new invoices are created as drafts. The tenant gets nothing (portal, alert or email) until you open the draft, check or edit it, and press Send to tenant (or Send all to tenants).
