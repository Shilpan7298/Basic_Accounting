from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from ...models import Company, Customer, Item, Supplier, TaxRate
from ...schemas.api import (
    CompanyIn,
    CustomerIn,
    CustomerOut,
    ItemIn,
    ItemOut,
    SupplierIn,
    SupplierOut,
    TaxRateIn,
    TaxRateOut,
)
from ...services import gstin as gstin_service
from ..deps import DbSession

router = APIRouter(tags=["masters"])


def _validated_gstin(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return gstin_service.validate(value)
    except gstin_service.InvalidGSTIN as exc:
        raise HTTPException(422, f"GSTIN rejected: {exc}") from exc


# --- company ---------------------------------------------------------------

@router.get("/company")
def get_company(db: DbSession):
    company = db.execute(select(Company)).scalars().first()
    if company is None:
        raise HTTPException(404, "company profile is not set up yet")
    return company


@router.put("/company")
def upsert_company(payload: CompanyIn, db: DbSession):
    gstin = _validated_gstin(payload.gstin)
    company = db.execute(select(Company)).scalars().first()
    data = payload.model_dump()
    data["gstin"] = gstin
    if gstin:
        # The state code is derived, never typed — it decides every tax split.
        data["state_code"] = gstin[:2]
        data["state_name"] = gstin_service.state_name(gstin[:2])

    if company is None:
        company = Company(**data)
        db.add(company)
    else:
        for key, value in data.items():
            setattr(company, key, value)
    db.commit()
    db.refresh(company)
    return company


# --- customers -------------------------------------------------------------

@router.get("/customers", response_model=list[CustomerOut])
def list_customers(db: DbSession, q: str | None = None):
    stmt = select(Customer).where(Customer.is_active.is_(True)).order_by(Customer.name)
    if q:
        stmt = stmt.where(Customer.name.ilike(f"%{q}%"))
    return list(db.execute(stmt).scalars())


@router.post("/customers", response_model=CustomerOut, status_code=201)
def create_customer(payload: CustomerIn, db: DbSession):
    gstin = _validated_gstin(payload.gstin)
    data = payload.model_dump()
    data["gstin"] = gstin
    if gstin:
        data["state_code"] = gstin[:2]
        data["place_of_supply_state_code"] = payload.place_of_supply_state_code or gstin[:2]
    data["state_name"] = gstin_service.state_name(data.get("state_code"))
    customer = Customer(**data)
    db.add(customer)
    db.commit()
    db.refresh(customer)
    return customer


@router.put("/customers/{customer_id}", response_model=CustomerOut)
def update_customer(customer_id: str, payload: CustomerIn, db: DbSession):
    customer = db.get(Customer, customer_id)
    if customer is None:
        raise HTTPException(404, "customer not found")
    gstin = _validated_gstin(payload.gstin)
    data = payload.model_dump()
    data["gstin"] = gstin
    if gstin:
        data["state_code"] = gstin[:2]
        data["place_of_supply_state_code"] = payload.place_of_supply_state_code or gstin[:2]
    data["state_name"] = gstin_service.state_name(data.get("state_code"))
    for key, value in data.items():
        setattr(customer, key, value)
    db.commit()
    db.refresh(customer)
    return customer


# --- suppliers -------------------------------------------------------------

@router.get("/suppliers", response_model=list[SupplierOut])
def list_suppliers(db: DbSession, q: str | None = None):
    stmt = select(Supplier).where(Supplier.is_active.is_(True)).order_by(Supplier.name)
    if q:
        stmt = stmt.where(Supplier.name.ilike(f"%{q}%"))
    return list(db.execute(stmt).scalars())


@router.post("/suppliers", response_model=SupplierOut, status_code=201)
def create_supplier(payload: SupplierIn, db: DbSession):
    gstin = _validated_gstin(payload.gstin)
    data = payload.model_dump()
    data["gstin"] = gstin
    if gstin:
        data["state_code"] = gstin[:2]
    data["state_name"] = gstin_service.state_name(data.get("state_code"))
    supplier = Supplier(**data)
    db.add(supplier)
    db.commit()
    db.refresh(supplier)
    return supplier


# --- items -----------------------------------------------------------------

@router.get("/items", response_model=list[ItemOut])
def list_items(db: DbSession, q: str | None = None):
    stmt = select(Item).where(Item.is_active.is_(True)).order_by(Item.sku)
    if q:
        stmt = stmt.where(Item.description.ilike(f"%{q}%"))
    return list(db.execute(stmt).scalars())


@router.post("/items", response_model=ItemOut, status_code=201)
def create_item(payload: ItemIn, db: DbSession):
    if db.execute(select(Item).where(Item.sku == payload.sku)).scalars().first():
        raise HTTPException(409, f"an item with SKU {payload.sku} already exists")
    item = Item(**payload.model_dump())
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


# --- tax rates -------------------------------------------------------------

@router.get("/tax-rates", response_model=list[TaxRateOut])
def list_tax_rates(db: DbSession, hsn: str | None = Query(None)):
    stmt = select(TaxRate).order_by(TaxRate.hsn_code, TaxRate.effective_from.desc())
    if hsn:
        stmt = stmt.where(TaxRate.hsn_code == hsn)
    return list(db.execute(stmt).scalars())


@router.post("/tax-rates", response_model=TaxRateOut, status_code=201)
def create_tax_rate(payload: TaxRateIn, db: DbSession):
    open_ended = db.execute(
        select(TaxRate).where(
            TaxRate.hsn_code == payload.hsn_code, TaxRate.effective_to.is_(None)
        )
    ).scalars().first()
    if open_ended and payload.effective_to is None:
        # Two open-ended rows for one HSN makes "the rate on this date"
        # ambiguous, so close the previous one automatically.
        if payload.effective_from <= open_ended.effective_from:
            raise HTTPException(
                422,
                f"HSN {payload.hsn_code} already has an open-ended rate from "
                f"{open_ended.effective_from}; the new rate must start after it",
            )
        open_ended.effective_to = payload.effective_from

    rate = TaxRate(**payload.model_dump())
    db.add(rate)
    db.commit()
    db.refresh(rate)
    return rate


@router.get("/gstin/validate")
def validate_gstin(value: str):
    try:
        normalised = gstin_service.validate(value)
    except gstin_service.InvalidGSTIN as exc:
        return {"valid": False, "reason": str(exc)}
    code = normalised[:2]
    return {
        "valid": True,
        "gstin": normalised,
        "state_code": code,
        "state_name": gstin_service.state_name(code),
    }
