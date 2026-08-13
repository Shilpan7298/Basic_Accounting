# Urjapod Order & Invoicing System

## What this is
An internal system for Urjapod Energy Private Limited (battery energy storage
systems, Ahmedabad, Gujarat) covering the full commercial document chain:

Sales side:   Customer PO (PDF) → extract → proforma invoice(s) per payment
              terms → dashboard w/ delivery countdown → tax invoice (HSN + GST)
Purchase side: Supplier offer + final negotiated rates → purchase order (Word)
Both sides:   Item / customer / supplier masters, tax compliance, management
              reports, periodic Tally-compatible export for the CA and auditors.

## Non-negotiable architectural constraints

1. **The AI extraction layer is pluggable.** All document understanding goes
   through one interface (`DocumentExtractor`) with swappable adapters:
   `AnthropicExtractor`, `OpenAICompatibleExtractor` (works with Ollama /
   vLLM / LM Studio on localhost), and `ManualExtractor` (a form). Selected by
   config, never by import. No provider SDK may be imported anywhere outside
   `extractors/`. Every adapter returns the same validated Pydantic schema.
   Assume that within a year the whole thing runs on a local model with no
   internet.

2. **Documents are rendered from user-editable templates, never from code.**
   Word output via `docxtpl` (Jinja2 syntax inside a real .docx the user can
   edit in Word). PDF output by rendering the same data through an HTML/Jinja
   template with WeasyPrint. Templates live in `templates/documents/`, are
   versioned, and the template version used is stored on every generated
   document so a reprint reproduces the original byte-for-byte intent.
   Adding a new template must require zero code changes.

3. **Financial records are append-only.** Nothing is hard-deleted or silently
   edited. Every mutation writes to an `audit_events` table (who, when, before,
   after). Indian law requires accounting software to maintain a tamper-evident
   edit log that cannot be disabled — design for that from day one, don't
   retrofit it.

4. **Document numbering is a first-class concern.** Separate series for
   proforma / tax invoice / credit note / purchase order. Per financial year
   (Apr–Mar). Sequential, gapless, no reuse. Cancelled invoices keep their
   number and are marked cancelled. Numbers are allocated inside the same DB
   transaction that creates the document, with a row lock — no race conditions.

5. **AI never commits financial data unattended.** Extraction produces a draft
   with per-field confidence. A human sees the source PDF side by side with the
   extracted fields, corrects, and approves. Only then does a record exist.

## Domain rules — India / GST

- Place of supply decides the tax split: intra-state (Gujarat → Gujarat) = CGST
  + SGST; inter-state = IGST. Derive from the customer's GSTIN state code vs
  Urjapod's (24 = Gujarat). Never let a user pick the split manually.
- Every item master carries an HSN code. GST rates live in a `tax_rates` table
  keyed by (HSN, effective_from) — never hardcoded, because rates change and
  old invoices must still reprint at the rate that applied on their date.
- GSTIN: validate the 15-character format and checksum on entry.
- Proforma invoices are **not** tax documents: separate series, no GST
  liability, clearly labelled, excluded from all GST reports and Tally export.
- Support reverse charge flag, exports / LUT (zero-rated), and TCS where
  applicable. Round-off line to the nearest rupee on the invoice total.
- E-invoicing (IRN/QR) and e-way bills: do **not** implement now, but keep
  `irn`, `ack_no`, `qr_payload`, `eway_bill_no` nullable columns on the invoice
  model and isolate the invoice-finalisation step so a call can be inserted
  later.
- Batteries: lithium-ion cells/packs are HSN 8507; confirm exact sub-heading and
  current rate against the item master, don't assume.

## Tally integration
- Export is **Tally XML** (`<ENVELOPE>` / `VOUCHER` format importable via
  Tally Prime's Import Data, and postable to the Tally HTTP listener on
  port 9000). Support both file export and direct HTTP post.
- Ledger names in the XML must match the ledgers already in Tally *exactly*, so
  there is a `tally_ledger_mappings` table: customer → sales ledger, supplier →
  purchase ledger, tax component → duty ledger, etc. No hardcoded ledger names.
- Every voucher carries the source document's GUID as `REMOTEID` so re-running
  an export is idempotent and never double-posts.
- Export runs for a date range, produces a manifest of what was included, and
  records that those documents were exported.

## Tech stack
- Backend: Python 3.12, FastAPI, SQLAlchemy 2.x, Alembic migrations, Pydantic v2
- DB: PostgreSQL (Decimal/NUMERIC for all money — never float, ever)
- Frontend: React + TypeScript + Vite, TanStack Query, Tailwind
- Docs: docxtpl (Word), WeasyPrint (PDF), openpyxl (Excel reports)
- Background work: simple APScheduler jobs; no Celery until it's needed
- Tests: pytest, with a `tests/fixtures/` folder of real anonymised POs
- Everything runs offline behind a firewall except the (optional) cloud
  extractor. Single `docker compose up`.

## Conventions
- Money: `Decimal`, stored as NUMERIC(18,4), rounded only at presentation.
- All dates stored UTC, displayed IST.
- Domain logic lives in `services/`, never in route handlers or React.
- One Alembic migration per schema change, always reversible.
- Every new endpoint gets a test. Every domain rule above gets a test.

## Do not
- Do not scaffold the whole app at once. One vertical slice at a time.
- Do not add auth providers, multi-tenancy, or microservices. Single company,
  handful of users, one database.
- Do not compute tax anywhere except the single `tax_engine` module.
- Do not put provider-specific prompt text outside `extractors/prompts/`.

---

## Decisions taken (answers to the kickoff questions)

These were confirmed by the business owner. Do not re-derive them.

**Document numbering format.** `URJ/25-26/0042` — prefix, financial year in
`YY-YY` form, 4-digit zero-padded sequence. Separate series per document type,
each with its own document-type token:

| Document       | Series key    | Example              |
|----------------|---------------|----------------------|
| Tax invoice    | `TAX_INVOICE` | `URJ/25-26/0042`     |
| Proforma       | `PROFORMA`    | `URJ/PI/25-26/0007`  |
| Credit note    | `CREDIT_NOTE` | `URJ/CN/25-26/0003`  |
| Purchase order | `PURCHASE_ORDER` | `URJ/PO/25-26/0011` |

The format is a config-driven pattern string on the `document_series` row
(`{prefix}/{fy}/{seq:04d}`), never a hardcoded f-string, so it can change
without a code change.

**Payment terms language.** Customer POs use percentage splits tied to named
events — "30% advance along with PO, 60% before dispatch, 10% after
commissioning". The parser targets that dialect. Anything it cannot parse to
100% falls through to the manual schedule builder; it never guesses.

**Tally ledgers.** The `tally_ledger_mappings` table is seeded with typical
Gujarat-company placeholder names and edited through the UI. Nothing in the
exporter may read a ledger name from anywhere else.

## Environment reality (deviations from the stack list, and why)

- **Python 3.11** is what the dev container ships. The code targets `>=3.11`;
  nothing used requires 3.12.
- **SQLite is supported for dev/test, PostgreSQL for real use.** `docker
  compose up` runs Postgres. Money is `NUMERIC(18,4)` on both; the SQLite path
  registers a `Decimal`-preserving type decorator so no test can pass on
  SQLite while failing on Postgres arithmetic. Row-lock numbering uses
  `SELECT ... FOR UPDATE` on Postgres and a serialized write transaction on
  SQLite — the same `NumberingService` API, one branch, tested both ways.
- **WeasyPrint works in this container** (pango/cairo present, verified
  rendering a real PDF). It is the only PDF renderer; there is no fallback
  path to keep in sync.
