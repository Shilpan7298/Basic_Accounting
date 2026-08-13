# Urjapod Order & Invoicing System — Architecture & Milestone Plan

Status: **approved**, kickoff questions answered (see `CLAUDE.md` → *Decisions taken*).
This document is the contract. When behaviour drifts from what's wanted, the fix
belongs here or in `CLAUDE.md` first, and in code second.

---

## 0. Shape of the system

```
                      ┌──────────────────────────────────────────┐
  customer PO (PDF) ─▶ │ extractors/  DocumentExtractor           │
  supplier offer(PDF)─▶│  Anthropic | OpenAICompatible | Manual   │
                      └───────────────┬──────────────────────────┘
                                      │ ExtractionResult (draft + confidence)
                                      ▼
                      ┌──────────────────────────────────────────┐
                      │ human review screen (PDF ⇆ fields)       │  ← nothing is
                      └───────────────┬──────────────────────────┘     committed
                                      │ approve                        before here
                                      ▼
   masters ─────────▶  ┌─────────────────────────────┐
   (customer/supplier/ │ services/  domain logic     │
    item/tax_rates)    │  orders · payment_terms ·   │
                       │  numbering · tax_engine ·   │
                       │  invoices · purchase ·      │
                       │  tally · reports            │
                       └───────┬─────────────┬───────┘
                               │             │
                    audit_events│             │ render_context
                               ▼             ▼
                       append-only log   templates/documents/
                                          docxtpl (.docx) + Jinja HTML→PDF
```

Three rules make the diagram true rather than aspirational:

* Route handlers do **nothing** but validate input, call one service, and shape
  the response. No `Decimal` arithmetic, no template names, no SQL.
* `tax_engine` is the only module that may multiply a rate by an amount.
* `extractors/` is the only package that may import a model-provider SDK or
  hold provider prompt text.

---

## 1. Data model

Money is `NUMERIC(18,4)` everywhere and maps to `Decimal`. Timestamps are
`TIMESTAMP WITH TIME ZONE` stored UTC. Every table carries `id` (UUID pk),
`created_at`, `updated_at`. "Append-only" tables additionally forbid `DELETE`
at the service layer and record a reversal row instead.

### 1.1 Masters

**`company`** — the single Urjapod row. `legal_name`, `gstin`, `state_code`
(`24`), `pan`, `cin`, address lines, `bank_name`, `bank_account_no`,
`bank_ifsc`, `logo_path`, `fy_start_month` (=4). Single row by convention; the
tax engine reads `state_code` from here to decide intra vs inter-state.

**`customers`** — `name`, `gstin` (nullable for unregistered), `state_code`
(derived from GSTIN, overridable for unregistered), `place_of_supply_state_code`,
billing + shipping address blocks, `payment_terms_default` (free text),
`is_sez`, `is_export`, `contact_name`, `email`, `phone`.

**`suppliers`** — same shape as customers minus the sales-specific bits, plus
`msme_registration_no` (affects payment-days reporting).

**`items`** — `sku`, `description`, `hsn_code`, `uom`, `default_unit_price`,
`is_service` (SAC vs HSN), `spec_json` (battery chemistry, Ah, V — free-form,
printed on documents). HSN is mandatory; nothing can be invoiced without one.

**`tax_rates`** — `hsn_code`, `effective_from` (date), `effective_to`
(nullable), `rate_percent`, `cess_percent`, `description`. Lookup is
"the row for this HSN whose validity window contains the *document date*",
which is what makes a 2024 invoice reprint at the 2024 rate.
Uniqueness: no two open-ended rows for the same HSN.

### 1.2 Numbering

**`document_series`** — one row per (`series_key`, `financial_year`).
Columns: `series_key` (`TAX_INVOICE` | `PROFORMA` | `CREDIT_NOTE` |
`PURCHASE_ORDER`), `financial_year` (`25-26`), `pattern`
(`URJ/{fy}/{seq:04d}`), `next_seq`, `is_locked`.
Unique on (`series_key`, `financial_year`).

Allocation contract:

```
with db.begin():                       # same transaction as the document insert
    row = SELECT ... FOR UPDATE        # Postgres; SQLite: serialized write txn
    number = row.pattern.format(fy=..., seq=row.next_seq)
    row.next_seq += 1
    db.add(Document(number=number, ...))
```

Gapless because the increment and the insert commit or roll back together.
No reuse because `next_seq` never decreases. Cancellation sets
`status='cancelled'` and keeps the number.

### 1.3 Sales side

**`sales_orders`** — the customer PO once approved.
`customer_id`, `customer_po_number`, `customer_po_date`, `order_value`,
`currency`, `delivery_due_date`, `payment_terms_text` (verbatim from the PO),
`status`, `source_document_id` (→ the uploaded PDF), `extraction_id`,
`notes`.

`status` enum and the only legal transitions:

```
draft ──approve──▶ received ──▶ in_production ──▶ dispatched ──▶ invoiced ──▶ paid
   │                   │              │               │
   └──────────────── cancelled ◀──────┴───────────────┘        (partially_paid
                                                                sits between
                                                                invoiced & paid)
```

Transitions are a table in `services/orders.py`, not `if` statements scattered
around. An illegal transition raises; every legal one writes an audit event.

**`sales_order_lines`** — `sales_order_id`, `line_no`, `item_id` (nullable
until mapped to a master), `description`, `hsn_code`, `qty`, `uom`,
`unit_price`, `discount_percent`, `line_total`.

**`payment_milestones`** — how payment terms become a proforma schedule.
`sales_order_id`, `seq`, `label` ("Advance along with PO"), `trigger_event`
(`ON_PO` | `BEFORE_DISPATCH` | `ON_DELIVERY` | `AFTER_COMMISSIONING` |
`NET_DAYS` | `MANUAL`), `percent`, `amount`, `due_date` (nullable, computed
when the trigger is date-derivable), `status` (`pending` | `invoiced` |
`received`), `proforma_invoice_id` (nullable).

Invariant, enforced in the service and tested: the milestone percents for an
order sum to exactly 100, and the milestone amounts sum to exactly the order
value — the last milestone absorbs the rounding remainder so no rupee is lost.

**`invoices`** — both proforma and tax invoices, discriminated by `doc_type`
(`PROFORMA` | `TAX_INVOICE` | `CREDIT_NOTE`), because they share 90% of their
columns and every report then has one place to look.
`number`, `series_key`, `financial_year`, `doc_type`, `invoice_date`,
`customer_id`, `sales_order_id`, `payment_milestone_id` (proforma only),
`place_of_supply_state_code`, `is_reverse_charge`, `is_export`, `lut_no`,
`taxable_value`, `cgst_amount`, `sgst_amount`, `igst_amount`, `cess_amount`,
`round_off`, `grand_total`, `amount_in_words`, `status` (`draft` | `issued` |
`cancelled`), `template_name`, `template_version`, `render_context_json`,
`pdf_path`, `docx_path`, `guid` (stable, used as Tally `REMOTEID`),
and the e-invoicing placeholders `irn`, `ack_no`, `qr_payload`,
`eway_bill_no` — all nullable, all unused for now.

`render_context_json` is the reason a reprint reproduces the original: the
exact dict handed to the template is frozen on the row at issue time.

**`invoice_lines`** — `invoice_id`, `line_no`, `item_id`, `description`,
`hsn_code`, `qty`, `uom`, `unit_price`, `discount_percent`, `taxable_value`,
`gst_rate_percent`, `cgst_amount`, `sgst_amount`, `igst_amount`,
`cess_amount`, `line_total`. Per-line tax, because GST reports are per-HSN.

**`receipts`** — money actually in the bank. `customer_id`, `sales_order_id`,
`invoice_id` (nullable — advances arrive against a proforma), `amount`,
`received_on`, `mode`, `reference`, `notes`. A tax invoice's "already
received" figure is the sum of receipts on the order dated on or before it.

### 1.4 Purchase side

**`supplier_offers`** — the extracted quote. `supplier_id`, `offer_ref`,
`offer_date`, `validity_date`, `currency`, `status` (`draft` | `approved` |
`ordered`), `source_document_id`, `extraction_id`.
**`supplier_offer_lines`** — offered line plus `negotiated_unit_price`, so the
final rate lives next to the quoted one and the delta is visible.

**`purchase_orders`** — `number` (from the `PURCHASE_ORDER` series),
`supplier_id`, `supplier_offer_id`, `po_date`, `delivery_due_date`,
`payment_terms_text`, `taxable_value`, tax columns as above, `grand_total`,
`status` (`draft` | `issued` | `cancelled`), `template_name`,
`template_version`, `render_context_json`, `docx_path`, `pdf_path`, `guid`.
**`purchase_order_lines`** — mirrors `invoice_lines`.

### 1.5 Cross-cutting

**`documents`** — every uploaded or generated file. `kind`
(`UPLOAD_CUSTOMER_PO` | `UPLOAD_SUPPLIER_OFFER` | `GENERATED_INVOICE` |
`GENERATED_PO`), `filename`, `content_type`, `size_bytes`, `sha256`,
`storage_path`. Uploads are content-addressed, so the same PO uploaded twice
is one blob.

**`extractions`** — one row per extraction attempt. `document_id`,
`extractor_name`, `model_name`, `schema_name`, `raw_response` (text, kept for
debugging bad local-model output), `parsed_json`, `field_confidence_json`,
`overall_confidence`, `status` (`pending` | `succeeded` | `failed` |
`needs_review` | `approved`), `error_text`, `duration_ms`.
Keeping the raw response is what lets you diagnose a local model six months
later without re-running it.

**`audit_events`** — append-only. `entity_type`, `entity_id`, `action`
(`create` | `update` | `status_change` | `cancel` | `issue` | `export`),
`actor`, `occurred_at`, `before_json`, `after_json`, `context_json`.
Written by a SQLAlchemy session hook plus explicit service calls, so a mutation
that forgets to log is a test failure, not a silent gap. No API deletes it; no
config disables it.

**`tally_ledger_mappings`** — `scope` (`CUSTOMER` | `SUPPLIER` | `TAX` |
`ITEM_GROUP` | `ROUNDOFF` | `BANK`), `local_key` (customer id, `CGST`, …),
`tally_ledger_name`, `tally_parent_group`, `notes`.
Unique on (`scope`, `local_key`).

**`tally_exports`** — `date_from`, `date_to`, `voucher_count`, `manifest_json`,
`xml_path`, `delivery` (`FILE` | `HTTP_POST`), `posted_at`, `response_text`.
**`tally_export_items`** — `export_id`, `document_type`, `document_id`,
`remote_id`. Unique on (`document_type`, `document_id`, `export_id`), and the
exporter skips documents already present in a *successful* export unless
explicitly asked to re-export — that is the idempotency guard on our side, and
`REMOTEID` is the guard on Tally's.

---

## 2. The `DocumentExtractor` interface

```python
class DocumentExtractor(Protocol):
    name: str
    def extract(self, req: ExtractionRequest) -> ExtractionResult[T]: ...
    def health(self) -> ExtractorHealth: ...
```

`ExtractionRequest` carries the raw bytes, the mime type, pre-extracted text
(pypdf, so a text-layer PDF never needs vision), and the target schema name.
`ExtractionResult` carries `data: T | None`, `field_confidence: dict[str,
float]`, `overall_confidence`, `raw_response`, `warnings`, `status`.

Selection is `EXTRACTOR=anthropic|openai_compatible|manual` in config, resolved
through a registry. Nothing imports an adapter module directly.

### 2.1 PO extraction schema (`CustomerPOExtraction`)

```
customer_name: str
customer_gstin: str | None            # validated format + checksum
customer_address: str | None
po_number: str
po_date: date
currency: str = "INR"
delivery_due_date: date | None
delivery_location: str | None
payment_terms_text: str | None        # verbatim, never normalised here
lines: list[POLine]                   # description, hsn_code?, qty, uom,
                                      # unit_price, discount_percent?, line_total?
subtotal: Decimal | None
grand_total: Decimal | None
notes: str | None
```

Validators that run on *every* adapter's output, so a local model gets held to
the same bar as a cloud one:
* `po_number` non-empty and stripped of label noise ("PO No.:" prefix removed)
* `po_date` parsed from Indian formats (`dd/mm/yyyy`, `dd-mm-yy`, `12 Apr 2025`)
  — never `mm/dd`, which is the classic silent corruption
* GSTIN format + checksum, else the field is dropped to `None` with a warning
  rather than saved wrong
* `sum(line_total)` vs `subtotal` — a mismatch beyond ₹1 lowers
  `overall_confidence` and forces `needs_review`
* every `Decimal` parsed from strings with `,` and `₹` stripped

### 2.2 Supplier-offer schema (`SupplierOfferExtraction`)

Same skeleton, different head: `supplier_name`, `supplier_gstin`,
`offer_ref`, `offer_date`, `validity_date`, `lead_time_days`,
`payment_terms_text`, `freight_terms`, `warranty_text`, and lines carrying
`quoted_unit_price` (negotiation happens in the app, not in the extractor).

### 2.3 How the two adapters satisfy it

**`AnthropicExtractor`** — sends the PDF text (or the document itself when
there is no text layer) with a tool/JSON-schema-constrained response derived
from the Pydantic model, so the shape is enforced by the API. Prompt text lives
in `extractors/prompts/`. Confidence: the model is asked for a per-field
confidence and the value is clamped and cross-checked against the arithmetic
validators — a model claiming 0.99 on a total that doesn't add up gets
overruled.

**`OpenAICompatibleExtractor`** — same prompt, `POST /v1/chat/completions`
against `OPENAI_BASE_URL` (Ollama, vLLM, LM Studio), `response_format:
json_object` when supported. Small local models will emit prose around the
JSON, trailing commas, `NaN`, markdown fences, and occasionally two JSON
objects. So parsing is a ladder:

1. `json.loads` the whole body
2. strip ``` fences, retry
3. brace-matching scan for the first balanced JSON object, retry
4. repair pass: trailing commas, single quotes, `NaN`/`Infinity` → `null`
5. one bounded re-ask with the validation error appended ("your JSON failed:
   `lines.0.qty` is not a number — return only valid JSON")
6. give up → `status=failed`, `raw_response` persisted, the UI opens the
   **manual form** on the same review screen

That last step is the point: a malformed local response degrades to a human
typing five fields, not to a stack trace or a wrong invoice.

**`ManualExtractor`** — returns an empty draft with confidence 0. It exists so
the review screen has exactly one code path, and so the app is fully usable
with no model at all.

---

## 3. Template contract

Templates live in `templates/documents/<doc_type>/<name>-v<N>.docx|.html` and
are discovered by scanning that directory — adding one is a file drop, no code
change, no registry edit. The version in the filename is what gets frozen onto
the document row.

Both the Word and the HTML template for a document type receive **the same
context dict**, so the PDF and the .docx can never disagree:

```
company{legal_name,gstin,address_lines,bank_*,logo_path}
document{type,number,date,due_date,is_proforma,title,notes,po_reference}
party{name,gstin,billing_address_lines,shipping_address_lines,state_name,...}
lines[]{no,description,hsn_code,qty,uom,unit_price,discount_percent,
        taxable_value,gst_rate_percent,cgst,sgst,igst,cess,total}
tax{is_intra_state,cgst_total,sgst_total,igst_total,cess_total,
    hsn_summary[]{hsn,taxable_value,rate,cgst,sgst,igst}}
totals{taxable_value,tax_total,round_off,grand_total,amount_in_words}
payment{milestone_label,percent,already_received,balance_due,schedule[]}
flags{is_reverse_charge,is_export,lut_no}
meta{template_name,template_version,generated_at_ist,page_footer}
```

Rules that keep a non-technical editor from breaking one:

* Everything they need is already computed. There is no arithmetic in a
  template — `{{ totals.grand_total }}`, never `{{ x * y }}`.
* All money arrives pre-formatted as strings (`1,23,456.78`, Indian grouping)
  alongside the raw value, so a template can't reformat a `Decimal` wrongly.
* Loops are the only construct they need: `{% tr for line in lines %}` in a
  Word table row (docxtpl's row tag), `{% for %}` in HTML.
* An unknown variable renders empty instead of raising — but
  `POST /templates/{name}/validate` renders the template against a golden
  sample context and reports every undefined name, so "did I break it?" is a
  button, not a support call.
* `templates/documents/README.md` is written for the person editing in Word:
  the variable table above, three worked examples, and "save as .docx, keep the
  `-vN` suffix, bump N".

---

## 4. Tally XML export

Emits `<ENVELOPE>` → `<BODY><IMPORTDATA><REQUESTDESC>`
(`Vouchers` / `$$CurrentCompany`) → `<REQUESTDATA><TALLYMESSAGE>` with one
`<VOUCHER VCHTYPE="Sales" ACTION="Create" OBJVIEW="Invoice Voucher">` per tax
invoice. Debit the customer ledger for the grand total; credit the sales
ledger for the taxable value; credit each duty ledger for its component;
round-off to its own ledger. Ledger names come *only* from
`tally_ledger_mappings` — a missing mapping is a hard, itemised pre-flight
error listing exactly which keys to fill in, never a guessed name.

Idempotency is two-sided: `REMOTEID` = the document's stable `guid`, so Tally
itself treats a re-import as an update to the same object; and
`tally_export_items` records what has already gone, so the default run skips
it. `--force` is an explicit operator choice.

Delivery is a file download or `POST http://<tally-host>:9000`; the response
body is parsed for `<LINEERROR>` and stored on the export row. Proformas are
excluded structurally — the query filters `doc_type='TAX_INVOICE'`, and there
is a test asserting a proforma never appears in an export.

---

## 5. Milestones

Ordered so the riskiest assumption is tested first. Each ends in something
clickable.

| # | Slice | Ends with you being able to |
|---|-------|-----------------------------|
| **M1** | **Extraction spine** | Upload a PO PDF, see source ⇆ extracted fields side by side with per-field confidence, correct them, approve → a `SalesOrder` exists. Both adapters behind one env var; a test proves the app works with the cloud provider unreachable. |
| **M2** | **Payment terms → proforma** | See "30% advance, 60% before dispatch, 10% after commissioning" become three milestones summing to 100%, then generate proforma #1 as .docx **and** PDF with a real number from the series. Includes the numbering concurrency test. |
| **M3** | **Dashboard + countdown** | See every open order with days-to-delivery, milestone status, and what's overdue. |
| **M4** | **Tax invoice + tax engine** | Raise a tax invoice against the order: HSN-driven rates from `tax_rates`, CGST/SGST vs IGST derived from GSTIN state codes, round-off, already-received netted off. Tax engine tested to the rupee. |
| **M5** | **Purchase side** | Upload a supplier offer, negotiate rates line by line, emit a Word purchase order. |
| **M6** | **Tally XML export** | Pick a date range, see a manifest, download the XML or post it to :9000, re-run and prove nothing double-posts. |
| **M7** | **Management reports** | Sales/purchase registers, GSTR-1-shaped HSN summary, outstanding receivables, all to Excel. |

**Most likely to be wrong: M2.** Not the numbering — that's mechanical and
testable. The *parsing of payment-terms prose*. "30% advance along with PO,
60% before dispatch, 10% after commissioning" is the clean case; real POs say
"balance on delivery", "70% against proforma prior to despatch, balance 30%
within 30 days of commissioning subject to satisfactory performance", or put
the terms in a scanned annexure. Percentages that sum to 90 or 110 are common.
The mitigation is structural rather than clever: the parser returns a
confidence, anything below threshold or not summing to exactly 100 goes to the
manual schedule builder, and the builder is built in M2 as a first-class
screen — not a fallback bolted on later. Expect the parser's rule set to be
rewritten once real POs land in `tests/fixtures/`; expect the schedule *model*
to survive.

Second most likely: the assumption in M1 that customer POs have a text layer.
Scanned ones need vision, which changes the local-model story — a 7B text model
can't read a scan. That's why the extractor request carries both bytes and
extracted text from day one.
