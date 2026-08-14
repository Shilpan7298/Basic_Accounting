# Urjapod Order & Invoicing System

Internal system for **Urjapod Energy Private Limited** (battery energy storage,
Ahmedabad) covering the whole commercial document chain:

```
Sales     customer PO (PDF) → extract → review → order
                            → proforma invoice per payment milestone
                            → dashboard with delivery countdown
                            → GST tax invoice (HSN, CGST/SGST vs IGST)

Purchase  supplier offer (PDF) → extract → negotiate rates → purchase order (Word)

Both      item / customer / supplier masters · append-only audit log
          · Tally XML export for the CA · management reports to Excel
```

Design constraints and domain rules live in [`CLAUDE.md`](CLAUDE.md); the
architecture and milestone plan in [`docs/PLAN.md`](docs/PLAN.md).

---

## Quick start

```bash
make setup          # venv + backend deps + npm install
make seed DEMO=1    # schema, users, masters, GST rates, one worked example
make api            # http://localhost:8000  (docs at /docs)
make web            # http://localhost:5173  (in a second terminal)
```

Sign in with `shilpan` / `urjapod-dev-9812` (owner) or `bookkeeper` /
`urjapod-dev-4471` (accountant) to see the two roles.

**Installing it for real** — one office server, browser clients, with the
accountant unable to change history: see **[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)**.

Or the whole thing in containers, with Postgres:

```bash
cp .env.example .env
docker compose up --build     # front end on :8080, API on :8000
```

### What to click to verify it works

1. **Dashboard** — the seeded order `SIPL/PO/2025-26/0187` with a delivery
   countdown, ₹67,50,000 value and ₹47,25,000 still to bill.
2. **Upload** → pick `backend/tests/fixtures/customer_po_solaris.pdf`, document
   type *Customer purchase order* → **Upload and extract**.
3. The **review screen** opens with the PDF on the left and the fields on the
   right. With `EXTRACTOR=manual` (the default) the fields are blank — type
   them in, or set `EXTRACTOR=openai_compatible` with Ollama running to have
   them filled in. **Nothing is saved until you press Approve.**
4. Approve → you land on the order. Press **Parse into milestones** and watch
   "30% advance along with PO, 60% before dispatch, 10% after commissioning"
   become three milestones totalling exactly 100%.
5. **Raise proforma** on milestone 1 → download the PDF and the Word file.
   It is labelled *PROFORMA INVOICE* and carries no GST.
6. **Raise tax invoice** → CGST+SGST for a Gujarat customer, IGST for anyone
   else, rates driven by each line's HSN code.
7. **Tally** → *Preview*. It refuses to export until every ledger is mapped,
   and reports the proforma as excluded.
8. **Purchase** → upload `supplier_offer_voltcell.pdf`, negotiate a rate, and
   generate the Word purchase order.

---

## How it is put together

```
backend/app/
  extractors/     the DocumentExtractor interface + adapters + prompts
  services/       all domain logic — tax_engine, numbering, payment_terms, …
  rendering/      template discovery, context building, docx/PDF output
  api/routes/     thin HTTP handlers: validate, call one service, shape output
  models/         SQLAlchemy tables
  templates/documents/   the user-editable invoice and PO templates
frontend/src/     React + TypeScript + Vite + TanStack Query + Tailwind
```

Five things are enforced rather than merely intended, each with tests:

| Constraint | Where | Test |
|---|---|---|
| Extraction is pluggable; no provider SDK outside `extractors/` | `extractors/registry.py` | works with the cloud provider unreachable |
| Documents render from editable templates, versioned and frozen | `rendering/` | a reprint reproduces the original after master data changes |
| Financial records are append-only | `services/audit.py` | a mutation nobody logged is still logged |
| Numbering is gapless, per-FY, race-free | `services/numbering.py` | 12 concurrent threads, 12 distinct contiguous numbers |
| AI never commits financial data unattended | `api/routes/extraction.py` | no order exists until `/approve` |
| The accountant cannot change history | `services/permissions.py` | he gets 403 and the invoice is unchanged |
| Amendments are new versions, never overwrites | `services/versioning.py` | v1 survives; the ledger only moves on approval |

### Tax

`services/tax_engine.py` is the only module allowed to multiply a rate by an
amount. It derives CGST+SGST vs IGST from the customer's GSTIN state code
against Urjapod's (24 = Gujarat) — never from a user's choice — and reads rates
from the `tax_rates` table keyed by `(HSN, date)`, so a 2024 invoice reprints
at the 2024 rate.

### Money

`Decimal` throughout, `NUMERIC(18,4)` in the database, rounded only for
presentation, with a round-off line to the nearest rupee so the parts always
sum to the whole. The API returns money as **strings** and the front end never
parses them into a JavaScript `number`.

---

## Choosing an extractor

One environment variable decides which document-understanding backend runs:

```bash
EXTRACTOR=manual              # a form. No model, no internet. Always works.
EXTRACTOR=openai_compatible   # Ollama / vLLM / LM Studio on localhost
EXTRACTOR=anthropic           # the cloud adapter
```

Small local models emit malformed JSON. That is handled rather than hoped
about: markdown fences are stripped, prose around the object is discarded,
trailing commas and `NaN` are repaired, and there is exactly **one** bounded
re-ask with the validation error attached. If it still fails, the review screen
opens on the manual form with the raw response kept for diagnosis.

## Tally

Ledger names come only from the `tally_ledger_mappings` table — a missing
mapping is an itemised pre-flight error, never a guessed name. Idempotency is
two-sided: each voucher carries the document's stable GUID as `REMOTEID`, and
`tally_export_items` records what has already gone. Proformas are excluded
structurally, by `doc_type`.

## Testing

```bash
make test        # 276 tests
```

Every domain rule in `CLAUDE.md` has a test, including the role split and the
versioning rules. Sample anonymised POs and supplier
offers are in `backend/tests/fixtures/` — replace them with real ones and the
extraction tests get sharper.

## Commands

| | |
|---|---|
| `make setup` | install everything |
| `make seed` / `make seed DEMO=1` | masters only / plus a worked example |
| `make api` · `make web` | run the two halves |
| `make test` | backend test suite |
| `make migrate` · `make migration M="…"` | apply / create an Alembic migration |
| `make templates` · `make fixtures` | rebuild the starter .docx / sample PDFs |
| `make up` · `make down` | docker compose |

## Not built, on purpose

E-invoicing (IRN/QR) and e-way bills are **not** implemented. The nullable
`irn`, `ack_no`, `qr_payload` and `eway_bill_no` columns exist on the invoice
and the finalisation step is isolated, so the call can be inserted later without
reshaping anything.

No auth *providers* (OAuth/SSO), no multi-tenancy, no microservices — single
company, a handful of users, one database. Local username/password with three
roles is there, because role separation is the whole point of the deployment;
see [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

## Packaging

`.github/workflows/release.yml` builds a Windows `.exe` and a macOS `.dmg` on
real Windows and macOS runners — they cannot be cross-built from Linux. Tag a
release (`git tag v0.2.0 && git push --tags`) and both appear on the Releases
page. The installers are written and the CI verifies PDF rendering on each
platform before packaging, but the Windows service install and the macOS
bundle still need one run on real hardware before you depend on them.
