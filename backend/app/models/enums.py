from __future__ import annotations

from enum import StrEnum


class SeriesKey(StrEnum):
    TAX_INVOICE = "TAX_INVOICE"
    PROFORMA = "PROFORMA"
    CREDIT_NOTE = "CREDIT_NOTE"
    PURCHASE_ORDER = "PURCHASE_ORDER"


class DocType(StrEnum):
    PROFORMA = "PROFORMA"
    TAX_INVOICE = "TAX_INVOICE"
    CREDIT_NOTE = "CREDIT_NOTE"


class OrderStatus(StrEnum):
    DRAFT = "draft"
    RECEIVED = "received"
    IN_PRODUCTION = "in_production"
    DISPATCHED = "dispatched"
    INVOICED = "invoiced"
    PARTIALLY_PAID = "partially_paid"
    PAID = "paid"
    CANCELLED = "cancelled"


class InvoiceStatus(StrEnum):
    DRAFT = "draft"
    ISSUED = "issued"
    CANCELLED = "cancelled"


class MilestoneTrigger(StrEnum):
    ON_PO = "ON_PO"
    BEFORE_DISPATCH = "BEFORE_DISPATCH"
    ON_DELIVERY = "ON_DELIVERY"
    AFTER_COMMISSIONING = "AFTER_COMMISSIONING"
    NET_DAYS = "NET_DAYS"
    MANUAL = "MANUAL"


class MilestoneStatus(StrEnum):
    PENDING = "pending"
    INVOICED = "invoiced"
    RECEIVED = "received"


class DocumentKind(StrEnum):
    UPLOAD_CUSTOMER_PO = "UPLOAD_CUSTOMER_PO"
    UPLOAD_SUPPLIER_OFFER = "UPLOAD_SUPPLIER_OFFER"
    GENERATED_INVOICE = "GENERATED_INVOICE"
    GENERATED_PO = "GENERATED_PO"


class ExtractionStatus(StrEnum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    NEEDS_REVIEW = "needs_review"
    APPROVED = "approved"


class OfferStatus(StrEnum):
    DRAFT = "draft"
    APPROVED = "approved"
    ORDERED = "ordered"


class PurchaseOrderStatus(StrEnum):
    DRAFT = "draft"
    ISSUED = "issued"
    CANCELLED = "cancelled"


class LedgerScope(StrEnum):
    CUSTOMER = "CUSTOMER"
    SUPPLIER = "SUPPLIER"
    TAX = "TAX"
    ITEM_GROUP = "ITEM_GROUP"
    ROUNDOFF = "ROUNDOFF"
    BANK = "BANK"


class AuditAction(StrEnum):
    CREATE = "create"
    UPDATE = "update"
    STATUS_CHANGE = "status_change"
    CANCEL = "cancel"
    ISSUE = "issue"
    EXPORT = "export"
