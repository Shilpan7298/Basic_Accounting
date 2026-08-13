"""Request/response models for the HTTP layer.

Route handlers validate with these, call exactly one service, and shape the
result. No domain logic lives here or in the routes.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --- masters ---------------------------------------------------------------

class CompanyIn(BaseModel):
    legal_name: str
    trade_name: str | None = None
    gstin: str | None = None
    state_code: str = "24"
    pan: str | None = None
    cin: str | None = None
    address_line1: str | None = None
    address_line2: str | None = None
    city: str | None = None
    state_name: str | None = None
    pincode: str | None = None
    email: str | None = None
    phone: str | None = None
    bank_name: str | None = None
    bank_account_no: str | None = None
    bank_ifsc: str | None = None
    bank_branch: str | None = None


class CustomerIn(BaseModel):
    name: str
    gstin: str | None = None
    state_code: str | None = None
    place_of_supply_state_code: str | None = None
    billing_address: str | None = None
    shipping_address: str | None = None
    payment_terms_default: str | None = None
    is_sez: bool = False
    is_export: bool = False
    contact_name: str | None = None
    email: str | None = None
    phone: str | None = None


class CustomerOut(ORMModel):
    id: str
    name: str
    gstin: str | None
    state_code: str | None
    state_name: str | None
    place_of_supply_state_code: str | None
    billing_address: str | None
    shipping_address: str | None
    is_sez: bool
    is_export: bool
    is_active: bool


class SupplierIn(BaseModel):
    name: str
    gstin: str | None = None
    state_code: str | None = None
    billing_address: str | None = None
    msme_registration_no: str | None = None
    payment_terms_default: str | None = None
    contact_name: str | None = None
    email: str | None = None
    phone: str | None = None


class SupplierOut(ORMModel):
    id: str
    name: str
    gstin: str | None
    state_code: str | None
    state_name: str | None
    billing_address: str | None
    is_active: bool


class ItemIn(BaseModel):
    sku: str
    description: str
    hsn_code: str
    uom: str = "NOS"
    default_unit_price: Decimal | None = None
    is_service: bool = False
    spec_json: str | None = None


class ItemOut(ORMModel):
    id: str
    sku: str
    description: str
    hsn_code: str
    uom: str
    default_unit_price: Decimal | None
    is_service: bool
    is_active: bool


class TaxRateIn(BaseModel):
    hsn_code: str
    effective_from: date
    effective_to: date | None = None
    rate_percent: Decimal
    cess_percent: Decimal = Decimal("0")
    description: str | None = None


class TaxRateOut(ORMModel):
    id: str
    hsn_code: str
    effective_from: date
    effective_to: date | None
    rate_percent: Decimal
    cess_percent: Decimal
    description: str | None


# --- extraction ------------------------------------------------------------

class ExtractionOut(ORMModel):
    id: str
    document_id: str
    extractor_name: str
    model_name: str | None
    schema_name: str
    status: str
    overall_confidence: float | None
    parsed_json: dict[str, Any] | None
    field_confidence_json: dict[str, Any] | None
    warnings_json: list[Any] | None
    error_text: str | None
    duration_ms: int | None


class UploadResponse(BaseModel):
    document_id: str
    filename: str
    size_bytes: int
    sha256: str
    page_count: int
    has_text_layer: bool
    extraction: ExtractionOut


class ApproveExtractionIn(BaseModel):
    """The corrected payload the human signed off, verbatim."""

    corrected: dict[str, Any]


# --- orders ----------------------------------------------------------------

class OrderLineOut(ORMModel):
    id: str
    line_no: int
    description: str
    hsn_code: str | None
    qty: Decimal
    uom: str
    unit_price: Decimal
    discount_percent: Decimal
    line_total: Decimal


class MilestoneOut(ORMModel):
    id: str
    seq: int
    label: str
    trigger_event: str
    percent: Decimal
    amount: Decimal
    net_days: int | None
    due_date: date | None
    status: str
    proforma_invoice_id: str | None
    source: str


class OrderOut(ORMModel):
    id: str
    customer_id: str
    customer_po_number: str
    customer_po_date: date
    order_value: Decimal
    currency: str
    delivery_due_date: date | None
    payment_terms_text: str | None
    status: str
    notes: str | None
    lines: list[OrderLineOut] = Field(default_factory=list)
    milestones: list[MilestoneOut] = Field(default_factory=list)


class OrderSummary(BaseModel):
    id: str
    customer_po_number: str
    customer_name: str
    status: str
    order_value: Decimal
    delivery_due_date: date | None
    days_to_delivery: int | None
    billed: Decimal
    unbilled: Decimal
    received: Decimal
    next_milestone: str | None


class TransitionIn(BaseModel):
    target: str
    reason: str | None = None


class ManualMilestoneIn(BaseModel):
    label: str
    trigger_event: str = "MANUAL"
    percent: Decimal
    net_days: int | None = None


class BuildMilestonesIn(BaseModel):
    """Omit ``manual`` to parse the terms text; supply it to override."""

    manual: list[ManualMilestoneIn] | None = None


class ParsedTermsOut(BaseModel):
    confidence: float
    total_percent: Decimal
    is_usable: bool
    warnings: list[str]
    milestones: list[dict[str, Any]]


class BuildMilestonesOut(BaseModel):
    created: list[MilestoneOut]
    parsed: ParsedTermsOut | None
    needs_manual_schedule: bool


class ReceiptIn(BaseModel):
    amount: Decimal
    received_on: date
    mode: str = "NEFT"
    reference: str | None = None
    invoice_id: str | None = None
    notes: str | None = None


# --- invoices --------------------------------------------------------------

class InvoiceLineOut(ORMModel):
    id: str
    line_no: int
    description: str
    hsn_code: str | None
    qty: Decimal
    uom: str
    unit_price: Decimal
    discount_percent: Decimal
    taxable_value: Decimal
    gst_rate_percent: Decimal
    cgst_amount: Decimal
    sgst_amount: Decimal
    igst_amount: Decimal
    cess_amount: Decimal
    line_total: Decimal


class InvoiceOut(ORMModel):
    id: str
    number: str
    doc_type: str
    series_key: str
    financial_year: str
    invoice_date: date
    due_date: date | None
    customer_id: str
    sales_order_id: str | None
    payment_milestone_id: str | None
    place_of_supply_state_code: str | None
    is_reverse_charge: bool
    is_export: bool
    lut_no: str | None
    taxable_value: Decimal
    cgst_amount: Decimal
    sgst_amount: Decimal
    igst_amount: Decimal
    cess_amount: Decimal
    round_off: Decimal
    grand_total: Decimal
    amount_in_words: str | None
    status: str
    template_name: str | None
    template_version: int | None
    pdf_path: str | None
    docx_path: str | None
    guid: str
    lines: list[InvoiceLineOut] = Field(default_factory=list)


class CreateProformaIn(BaseModel):
    milestone_id: str
    invoice_date: date | None = None
    issue: bool = True


class CreateTaxInvoiceIn(BaseModel):
    invoice_date: date | None = None
    is_reverse_charge: bool = False
    lut_no: str | None = None
    credit_days: int | None = None
    issue: bool = True


class CancelIn(BaseModel):
    reason: str


# --- purchase --------------------------------------------------------------

class OfferLineOut(ORMModel):
    id: str
    line_no: int
    description: str
    hsn_code: str | None
    qty: Decimal
    uom: str
    quoted_unit_price: Decimal
    negotiated_unit_price: Decimal | None
    discount_percent: Decimal


class OfferOut(ORMModel):
    id: str
    supplier_id: str
    offer_ref: str | None
    offer_date: date | None
    validity_date: date | None
    currency: str
    lead_time_days: int | None
    payment_terms_text: str | None
    freight_terms: str | None
    warranty_text: str | None
    status: str
    lines: list[OfferLineOut] = Field(default_factory=list)


class NegotiateIn(BaseModel):
    """Keyed by line id so a re-ordered UI cannot mis-assign a price."""

    rates: dict[str, Decimal]


class CreatePOIn(BaseModel):
    po_date: date | None = None
    delivery_due_date: date | None = None
    delivery_location: str | None = None
    is_reverse_charge: bool = False
    notes: str | None = None
    issue: bool = True


class PurchaseOrderLineOut(ORMModel):
    id: str
    line_no: int
    description: str
    hsn_code: str | None
    qty: Decimal
    uom: str
    unit_price: Decimal
    taxable_value: Decimal
    gst_rate_percent: Decimal
    cgst_amount: Decimal
    sgst_amount: Decimal
    igst_amount: Decimal
    line_total: Decimal


class PurchaseOrderOut(ORMModel):
    id: str
    number: str
    financial_year: str
    supplier_id: str
    supplier_offer_id: str | None
    po_date: date
    delivery_due_date: date | None
    payment_terms_text: str | None
    taxable_value: Decimal
    cgst_amount: Decimal
    sgst_amount: Decimal
    igst_amount: Decimal
    round_off: Decimal
    grand_total: Decimal
    amount_in_words: str | None
    status: str
    docx_path: str | None
    pdf_path: str | None
    lines: list[PurchaseOrderLineOut] = Field(default_factory=list)


# --- tally -----------------------------------------------------------------

class LedgerMappingIn(BaseModel):
    scope: str
    local_key: str
    tally_ledger_name: str
    tally_parent_group: str | None = None
    notes: str | None = None


class LedgerMappingOut(ORMModel):
    id: str
    scope: str
    local_key: str
    tally_ledger_name: str
    tally_parent_group: str | None
    notes: str | None


class TallyExportIn(BaseModel):
    date_from: date
    date_to: date
    delivery: str = "FILE"
    force: bool = False


class TallyPreviewOut(BaseModel):
    voucher_count: int
    manifest: dict[str, Any]
    missing_mappings: list[dict[str, str]]


class TallyExportOut(ORMModel):
    id: str
    date_from: date
    date_to: date
    voucher_count: int
    xml_path: str | None
    delivery: str
    succeeded: bool
    response_text: str | None
    manifest_json: dict[str, Any] | None


# --- misc ------------------------------------------------------------------

class AuditEventOut(ORMModel):
    id: str
    entity_type: str
    entity_id: str
    action: str
    actor: str
    occurred_at: Any
    before_json: dict[str, Any] | None
    after_json: dict[str, Any] | None
    context_json: dict[str, Any] | None


class TemplateOut(BaseModel):
    doc_type: str
    name: str
    version: int
    kind: str
    path: str


class TemplateValidationOut(BaseModel):
    ok: bool
    template: str
    undefined_names: list[str]
    error: str | None
