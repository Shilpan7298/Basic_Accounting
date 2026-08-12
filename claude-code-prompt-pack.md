# Claude Code Prompt Pack — Urjapod Order & Invoicing System

Three things go into Claude Code, in this order:

1. **`CLAUDE.md`** — persistent project context. Claude Code reads this on every session. Put it at the repo root.
2. **The kickoff prompt** — run this once, in **plan mode** (`Shift+Tab` twice). It produces an architecture + milestone plan you review *before* any code exists.
3. **Milestone prompts** — one per vertical slice, each ending in something you can actually click.

The single biggest quality lever with Claude Code is refusing to let it write code before you've agreed on a plan. The second is giving it a way to check its own work (tests, sample POs, a running server).

---

## 1. `CLAUDE.md`

```markdown
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
```

---

## 2. Kickoff prompt (run in plan mode — `Shift+Tab` twice)

```
Read CLAUDE.md fully before responding.

Think hard about this, then produce a plan only — no code, no files.

I want:

1. A data model: every table, its columns and types, and the relationships.
   Pay particular attention to (a) how a purchase order's payment terms become
   a schedule of proforma-invoice milestones, (b) how an order moves through
   states from received → in production → dispatched → invoiced → paid, and
   (c) how a tax invoice links back to the order, the proforma(s) already
   raised, and the amounts already received.

2. The `DocumentExtractor` interface, in full, with the exact Pydantic schema a
   PO extraction returns and the exact schema a supplier-offer extraction
   returns. Show me how the Anthropic adapter and a local-Ollama adapter both
   satisfy it, and what happens when a local model returns malformed JSON.

3. The template contract: what variables a Word invoice template can use, what
   a purchase-order template can use, and how someone non-technical edits one
   without breaking it.

4. The Tally XML export design, including the ledger-mapping model and how
   idempotency is enforced.

5. A milestone breakdown. Each milestone must be a working vertical slice I can
   click through and judge, not a horizontal layer. Order them so the riskiest
   assumption is tested first. Tell me which milestone you think is most likely
   to be wrong and why.

Ask me any questions where a wrong guess would cost significant rework —
especially about our payment-terms language, our invoice numbering format, and
how our Tally ledgers are currently named. Don't guess on those.

Write the agreed plan to docs/PLAN.md when I approve it.
```

**Answer its questions properly.** The four things worth having ready before you start:

- 2–3 real customer POs (anonymised) and 2 supplier offers, in `tests/fixtures/`
- Your existing invoice, proforma, and PO documents as .docx — these *become* the templates
- A screenshot or export of your Tally ledger names
- Your invoice number format, exactly as used today (e.g. `URJ/25-26/0042`)

---

## 3. Milestone prompts

Run each in a fresh session. Start each with "Read CLAUDE.md and docs/PLAN.md."

**M1 — Extraction spine (riskiest thing first)**
```
Read CLAUDE.md and docs/PLAN.md.

Build milestone 1 only: upload a PO PDF, extract it through the
DocumentExtractor interface, and show a side-by-side review screen (source PDF
left, editable extracted fields right, confidence indicator per field). Saving
creates the SalesOrder.

Implement both the Anthropic adapter and the OpenAI-compatible adapter (so it
works against Ollama on localhost) behind the same interface, switched by an
env var. Write tests against the PDFs in tests/fixtures/ asserting the parsed
fields, and a test proving the app works with the cloud provider entirely
unreachable.

Then run it and tell me exactly what to click to verify.
```

**M2 — Payment terms → proforma**
```
Milestone 2: turn the order's payment terms into a milestone schedule, and
generate proforma invoice #1 from a docxtpl template into both .docx and PDF.
Include the numbering service with its concurrency test. Terms language to
handle is in docs/PAYMENT_TERMS.md — parse those, and fall back to a manual
schedule builder when confidence is low rather than guessing.
```

**M3 — Dashboard + countdown** · **M4 — Tax invoice with HSN/GST + tax engine tests** ·
**M5 — Purchase side: supplier offer → Word PO** · **M6 — Tally XML export** ·
**M7 — Management reports**

---

## Two habits that matter more than any prompt

**Make it prove things, not claim them.** End requests with "then run it and
tell me exactly what to click to verify" or "write a failing test first." Claude
Code is far more reliable when it has a feedback loop it can close by itself.

**Correct the plan, not the code.** When output drifts from what you wanted, the
fix usually belongs in `CLAUDE.md` or `docs/PLAN.md`, not in the next message.
Anything you find yourself saying twice should become a line in `CLAUDE.md`.
