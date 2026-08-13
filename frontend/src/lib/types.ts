/** Mirrors backend/app/schemas/api.py. Money is always a string. */

export type Money = string

export interface OrderSummary {
  id: string
  customer_po_number: string
  customer_name: string
  status: string
  order_value: Money
  delivery_due_date: string | null
  days_to_delivery: number | null
  billed: Money
  unbilled: Money
  received: Money
  next_milestone: string | null
}

export interface OrderLine {
  id: string
  line_no: number
  description: string
  hsn_code: string | null
  qty: Money
  uom: string
  unit_price: Money
  discount_percent: Money
  line_total: Money
}

export interface Milestone {
  id: string
  seq: number
  label: string
  trigger_event: string
  percent: Money
  amount: Money
  net_days: number | null
  due_date: string | null
  status: string
  proforma_invoice_id: string | null
  source: string
}

export interface Order {
  id: string
  customer_id: string
  customer_po_number: string
  customer_po_date: string
  order_value: Money
  currency: string
  delivery_due_date: string | null
  payment_terms_text: string | null
  status: string
  notes: string | null
  lines: OrderLine[]
  milestones: Milestone[]
}

export interface InvoiceLine {
  id: string
  line_no: number
  description: string
  hsn_code: string | null
  qty: Money
  uom: string
  unit_price: Money
  taxable_value: Money
  gst_rate_percent: Money
  cgst_amount: Money
  sgst_amount: Money
  igst_amount: Money
  line_total: Money
}

export interface Invoice {
  id: string
  number: string
  doc_type: 'PROFORMA' | 'TAX_INVOICE' | 'CREDIT_NOTE'
  financial_year: string
  invoice_date: string
  due_date: string | null
  customer_id: string
  sales_order_id: string | null
  taxable_value: Money
  cgst_amount: Money
  sgst_amount: Money
  igst_amount: Money
  round_off: Money
  grand_total: Money
  amount_in_words: string | null
  status: 'draft' | 'issued' | 'cancelled'
  pdf_path: string | null
  docx_path: string | null
  lines: InvoiceLine[]
}

export interface ParsedTerms {
  confidence: number
  total_percent: Money
  is_usable: boolean
  warnings: string[]
  milestones: {
    seq: number
    label: string
    trigger_event: string
    percent: Money
    net_days: number | null
    source_clause: string
  }[]
}

export interface BuildMilestonesResult {
  created: Milestone[]
  parsed: ParsedTerms | null
  needs_manual_schedule: boolean
}

export interface Extraction {
  id: string
  document_id: string
  extractor_name: string
  model_name: string | null
  schema_name: string
  status: 'pending' | 'succeeded' | 'failed' | 'needs_review' | 'approved'
  overall_confidence: number | null
  parsed_json: Record<string, any> | null
  field_confidence_json: Record<string, number> | null
  warnings_json: string[] | null
  error_text: string | null
  duration_ms: number | null
}

export interface UploadResponse {
  document_id: string
  filename: string
  size_bytes: number
  sha256: string
  page_count: number
  has_text_layer: boolean
  extraction: Extraction
}

export interface Customer {
  id: string
  name: string
  gstin: string | null
  state_code: string | null
  state_name: string | null
  place_of_supply_state_code: string | null
  billing_address: string | null
  is_sez: boolean
  is_export: boolean
  is_active: boolean
}

export interface Supplier {
  id: string
  name: string
  gstin: string | null
  state_code: string | null
  state_name: string | null
  billing_address: string | null
  is_active: boolean
}

export interface OfferLine {
  id: string
  line_no: number
  description: string
  hsn_code: string | null
  qty: Money
  uom: string
  quoted_unit_price: Money
  negotiated_unit_price: Money | null
  discount_percent: Money
}

export interface Offer {
  id: string
  supplier_id: string
  offer_ref: string | null
  offer_date: string | null
  validity_date: string | null
  lead_time_days: number | null
  payment_terms_text: string | null
  freight_terms: string | null
  warranty_text: string | null
  status: string
  lines: OfferLine[]
}

export interface PurchaseOrder {
  id: string
  number: string
  supplier_id: string
  po_date: string
  delivery_due_date: string | null
  taxable_value: Money
  cgst_amount: Money
  sgst_amount: Money
  igst_amount: Money
  grand_total: Money
  amount_in_words: string | null
  status: string
  docx_path: string | null
  pdf_path: string | null
  lines: {
    id: string
    line_no: number
    description: string
    hsn_code: string | null
    qty: Money
    uom: string
    unit_price: Money
    taxable_value: Money
    gst_rate_percent: Money
    line_total: Money
  }[]
}

export interface LedgerMapping {
  id: string
  scope: string
  local_key: string
  tally_ledger_name: string
  tally_parent_group: string | null
  notes: string | null
}

export interface TallyPreview {
  voucher_count: number
  manifest: {
    invoices: { id: string; number: string; date: string; customer: string; grand_total: string }[]
    skipped_already_exported: string[]
    excluded_proformas: number
    excluded_cancelled: number
  }
  missing_mappings: { scope: string; local_key: string; hint: string }[]
}

export interface ReportPayload {
  title: string
  columns: string[]
  rows: string[][]
  totals: Record<string, string>
}

export interface Health {
  status: string
  database: string
  extractor_selected: string
  extractors: { name: string; available: boolean; detail: string }[]
}
