"""The extraction spine (M1).

The assertions that matter most are the *degradation* ones: the app must work
with the cloud provider unreachable, and a local model returning junk must
produce a manual-entry draft rather than a stack trace or a wrong record.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

import httpx
import pytest

from app.extractors import get_extractor
from app.extractors.base import ExtractionRequest
from app.extractors.json_repair import JSONRepairFailed
from app.extractors.json_repair import loads as repair_loads
from app.extractors.manual import ManualExtractor
from app.extractors.openai_compatible import OpenAICompatibleExtractor
from app.extractors.pdf_text import extract_text, has_text_layer
from app.models import ExtractionStatus
from app.schemas.extraction import CustomerPOExtraction, SupplierOfferExtraction, parse_date

from .conftest import FIXTURES


# --- fixtures on disk ------------------------------------------------------

def test_every_fixture_has_a_text_layer():
    """If this fails, the local text-only model path is untestable."""
    for path in sorted(FIXTURES.glob("*.pdf")):
        assert has_text_layer(path.read_bytes()), path.name


def test_fixture_text_contains_the_fields_we_claim_to_extract():
    text = extract_text((FIXTURES / "customer_po_solaris.pdf").read_bytes())

    assert "SIPL/PO/2025-26/0187" in text
    assert "27AAACS7409B1ZN" in text
    assert "8507" in text
    assert "30% advance along with PO" in text


# --- the shared schema -----------------------------------------------------

def test_indian_dates_are_day_first():
    """05/04/2025 is 5 April, never 5 May. This is the classic silent corruption."""
    assert parse_date("05/04/2025") == date(2025, 4, 5)
    assert parse_date("04-08-2025") == date(2025, 8, 4)
    assert parse_date("30/09/2025") == date(2025, 9, 30)
    assert parse_date("18-07-2025") == date(2025, 7, 18)


def test_various_date_shapes_parse():
    assert parse_date("2025-08-04") == date(2025, 8, 4)
    assert parse_date("4 Aug 2025") == date(2025, 8, 4)
    assert parse_date("4th August 2025") == date(2025, 8, 4)
    assert parse_date("04/08/25") == date(2025, 8, 4)


def test_an_unparseable_date_is_none_not_a_guess():
    assert parse_date("sometime next quarter") is None
    assert parse_date("") is None
    assert parse_date(None) is None


def test_a_gstin_that_fails_the_checksum_is_dropped_not_stored():
    payload = {
        "customer_name": "Test Customer",
        "po_number": "PO-1",
        "customer_gstin": "27AAACS7409B1ZZ",  # wrong checksum
        "lines": [],
    }
    parsed = CustomerPOExtraction.model_validate(payload)
    assert parsed.customer_gstin is None


def test_a_valid_gstin_survives():
    parsed = CustomerPOExtraction.model_validate(
        {"customer_name": "T", "po_number": "1", "customer_gstin": "27AAACS7409B1ZN", "lines": []}
    )
    assert parsed.customer_gstin == "27AAACS7409B1ZN"


def test_label_noise_is_stripped_from_document_numbers():
    parsed = CustomerPOExtraction.model_validate(
        {"customer_name": "T", "po_number": "PO No.: SIPL/PO/2025-26/0187", "lines": []}
    )
    assert parsed.po_number == "SIPL/PO/2025-26/0187"


def test_money_strings_from_documents_are_parsed():
    parsed = CustomerPOExtraction.model_validate(
        {
            "customer_name": "T",
            "po_number": "1",
            "lines": [
                {"description": "x", "qty": "4", "unit_price": "12,50,000.00",
                 "line_total": "₹ 50,00,000.00"}
            ],
            "grand_total": "Rs. 79,65,000.00",
        }
    )
    assert parsed.lines[0].unit_price == Decimal("1250000.00")
    assert parsed.grand_total == Decimal("7965000.00")


def test_line_total_is_derived_when_the_document_omits_it():
    parsed = CustomerPOExtraction.model_validate(
        {"customer_name": "T", "po_number": "1",
         "lines": [{"description": "x", "qty": "4", "unit_price": "100", "discount_percent": "10"}]}
    )
    assert parsed.lines[0].line_total == Decimal("360")


def test_nonsense_hsn_codes_are_discarded():
    parsed = CustomerPOExtraction.model_validate(
        {"customer_name": "T", "po_number": "1",
         "lines": [
             {"description": "a", "hsn_code": "8507"},
             {"description": "b", "hsn_code": "85"},        # too short
             {"description": "c", "hsn_code": "HSN 8507"},  # digits extracted
             {"description": "d", "hsn_code": "not-an-hsn"},
         ]}
    )
    assert [line.hsn_code for line in parsed.lines] == ["8507", None, "8507", None]


def test_supplier_offer_lead_time_is_normalised_to_days():
    parsed = SupplierOfferExtraction.model_validate(
        {"supplier_name": "S", "lead_time_days": "28 days", "lines": []}
    )
    assert parsed.lead_time_days == 28


# --- the JSON repair ladder ------------------------------------------------

def test_clean_json_parses_on_the_first_rung():
    parsed, notes = repair_loads('{"a": 1}')
    assert parsed == {"a": 1}
    assert notes == []


def test_markdown_fences_are_stripped():
    parsed, notes = repair_loads('```json\n{"a": 1}\n```')
    assert parsed == {"a": 1}
    assert any("fence" in n for n in notes)


def test_prose_around_the_object_is_discarded():
    raw = 'Sure! Here is the extracted data:\n{"a": 1, "b": {"c": 2}}\nLet me know if you need more.'
    parsed, notes = repair_loads(raw)
    assert parsed == {"a": 1, "b": {"c": 2}}
    assert any("balanced" in n for n in notes)


def test_trailing_commas_and_python_literals_are_repaired():
    raw = 'Here you go: {"a": 1, "b": None, "c": True, "d": [1, 2,],}'
    parsed, notes = repair_loads(raw)
    assert parsed == {"a": 1, "b": None, "c": True, "d": [1, 2]}
    assert any("repair" in n for n in notes)


def test_nan_becomes_null_rather_than_crashing():
    parsed, _ = repair_loads('{"qty": NaN, "price": -Infinity,}')
    assert parsed == {"qty": None, "price": None}


def test_braces_inside_strings_do_not_confuse_the_matcher():
    parsed, _ = repair_loads('{"note": "use {curly} braces", "n": 1}')
    assert parsed["note"] == "use {curly} braces"


def test_unrepairable_output_raises_rather_than_returning_nonsense():
    for raw in ["", "   ", "I cannot read this document.", "{{{{"]:
        with pytest.raises(JSONRepairFailed):
            repair_loads(raw)


def test_the_raw_response_travels_with_the_failure():
    try:
        repair_loads("total gibberish with no object")
    except JSONRepairFailed as exc:
        assert exc.raw == "total gibberish with no object"


# --- adapters --------------------------------------------------------------

def test_the_registry_selects_by_config_not_import():
    assert get_extractor("manual").name == "manual"
    assert get_extractor("openai_compatible").name == "openai_compatible"
    assert get_extractor("anthropic").name == "anthropic"
    with pytest.raises(ValueError, match="unknown extractor"):
        get_extractor("does-not-exist")


def test_the_manual_extractor_always_works():
    result = ManualExtractor().extract(
        ExtractionRequest(content=b"", content_type="application/pdf", schema_name="customer_po")
    )
    assert result.status is ExtractionStatus.NEEDS_REVIEW
    assert result.overall_confidence == 0.0
    assert ManualExtractor().health().available


def test_the_app_works_with_the_cloud_provider_entirely_unreachable(monkeypatch):
    """The stated requirement, tested directly: no network, no crash, still usable."""
    from app.extractors.anthropic_extractor import AnthropicExtractor

    def _boom(*_args, **_kwargs):
        raise httpx.ConnectError("network is unreachable")

    monkeypatch.setattr(httpx.Client, "post", _boom)

    extractor = AnthropicExtractor(api_key="sk-test")
    result = extractor.extract(
        ExtractionRequest(
            content=b"%PDF-", content_type="application/pdf",
            schema_name="customer_po", text="x" * 200,
        )
    )

    assert result.status is ExtractionStatus.FAILED
    assert "unreachable" in (result.error_text or "")
    assert "manual" in " ".join(result.warnings)
    # And the fallback is genuinely available.
    from app.extractors import get_fallback

    assert get_fallback().health().available


def test_a_missing_api_key_degrades_instead_of_raising():
    from app.extractors.anthropic_extractor import AnthropicExtractor

    result = AnthropicExtractor(api_key=None).extract(
        ExtractionRequest(content=b"x", content_type="application/pdf",
                          schema_name="customer_po", text="x" * 200)
    )
    assert result.status is ExtractionStatus.FAILED
    assert "ANTHROPIC_API_KEY" in (result.error_text or "")


def test_local_model_unreachable_degrades_to_manual(monkeypatch):
    def _boom(*_args, **_kwargs):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx.Client, "post", _boom)

    result = OpenAICompatibleExtractor(base_url="http://localhost:11434/v1").extract(
        ExtractionRequest(content=b"x", content_type="application/pdf",
                          schema_name="customer_po", text="x" * 200)
    )
    assert result.status is ExtractionStatus.FAILED
    assert "could not reach the local model" in (result.error_text or "")


def _fake_completion(content: str):
    def _post(self, url, **kwargs):
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": content}}]},
            request=httpx.Request("POST", url),
        )

    return _post


def test_a_local_model_wrapping_json_in_prose_still_succeeds(monkeypatch):
    """The realistic small-model failure: correct data, sloppy envelope."""
    payload = {
        "customer_name": "Solaris Infra Projects Private Limited",
        "customer_gstin": "27AAACS7409B1ZN",
        "po_number": "SIPL/PO/2025-26/0187",
        "po_date": "04/08/2025",
        "payment_terms_text": "30% advance along with PO, 60% before dispatch, 10% after commissioning",
        "lines": [
            {"description": "BESS 100 kWh", "hsn_code": "8507", "qty": 4,
             "unit_price": 1250000, "line_total": 5000000}
        ],
        "subtotal": 5000000,
        "field_confidence": {"customer_name": 0.95, "po_number": 0.9, "po_date": 0.85},
    }
    monkeypatch.setattr(
        httpx.Client,
        "post",
        _fake_completion(f"Sure, here is the JSON:\n```json\n{json.dumps(payload)}\n```\nHope that helps!"),
    )

    result = OpenAICompatibleExtractor().extract(
        ExtractionRequest(content=b"x", content_type="application/pdf",
                          schema_name="customer_po", text="x" * 200)
    )

    assert result.succeeded
    assert result.data.po_number == "SIPL/PO/2025-26/0187"
    assert result.data.po_date == date(2025, 8, 4)
    assert result.data.lines[0].hsn_code == "8507"


def test_a_model_lying_about_confidence_is_overruled_by_arithmetic(monkeypatch):
    """Claimed 0.99 on a subtotal that does not match the lines."""
    payload = {
        "customer_name": "X", "po_number": "1", "po_date": "04/08/2025",
        "customer_gstin": "27AAACS7409B1ZN",
        "lines": [{"description": "a", "hsn_code": "8507", "qty": 1,
                   "unit_price": 100, "line_total": 100}],
        "subtotal": 999999,
        "field_confidence": {"customer_name": 0.99, "po_number": 0.99, "subtotal": 0.99},
    }
    monkeypatch.setattr(httpx.Client, "post", _fake_completion(json.dumps(payload)))

    result = OpenAICompatibleExtractor().extract(
        ExtractionRequest(content=b"x", content_type="application/pdf",
                          schema_name="customer_po", text="x" * 200)
    )

    assert result.status is ExtractionStatus.NEEDS_REVIEW
    assert result.overall_confidence < 0.7
    assert any("subtotal" in w for w in result.warnings)


def test_persistently_malformed_output_fails_honestly_after_one_retry(monkeypatch):
    calls: list[int] = []

    def _post(self, url, **kwargs):
        calls.append(1)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "I'm not able to produce JSON here."}}]},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx.Client, "post", _post)

    result = OpenAICompatibleExtractor().extract(
        ExtractionRequest(content=b"x", content_type="application/pdf",
                          schema_name="customer_po", text="x" * 200)
    )

    assert result.status is ExtractionStatus.FAILED
    assert len(calls) == 2, "expected exactly one bounded retry, not an unbounded loop"
    assert result.raw_response  # kept for diagnosing the model later
    assert "manual" in " ".join(result.warnings)


def test_a_retry_recovers_when_the_second_answer_is_valid(monkeypatch):
    good = {
        "customer_name": "X", "po_number": "1", "po_date": "04/08/2025",
        "customer_gstin": "27AAACS7409B1ZN",
        "lines": [{"description": "a", "hsn_code": "8507", "qty": 1,
                   "unit_price": 100, "line_total": 100}],
        "subtotal": 100,
        "field_confidence": {"customer_name": 0.9, "po_number": 0.9, "po_date": 0.9},
    }
    responses = ["not json at all", json.dumps(good)]

    def _post(self, url, **kwargs):
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": responses.pop(0)}}]},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx.Client, "post", _post)

    result = OpenAICompatibleExtractor().extract(
        ExtractionRequest(content=b"x", content_type="application/pdf",
                          schema_name="customer_po", text="x" * 200)
    )

    assert result.succeeded
    assert result.data.po_number == "1"
    assert any("re-asked" in w for w in result.warnings)


def test_a_text_only_local_model_refuses_a_scan_rather_than_hallucinating():
    result = OpenAICompatibleExtractor().extract(
        ExtractionRequest(content=b"%PDF-scan", content_type="application/pdf",
                          schema_name="customer_po", text="")
    )
    assert result.status is ExtractionStatus.FAILED
    assert "no text layer" in (result.error_text or "")


# --- persistence -----------------------------------------------------------

def test_a_failed_extraction_is_still_recorded(db, monkeypatch):
    """The raw response is the only evidence of what a model said. Keep it."""
    from app.models import DocumentKind
    from app.services import documents as doc_service

    monkeypatch.setenv("EXTRACTOR", "openai_compatible")

    def _post(self, url, **kwargs):
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "sorry, I cannot"}}]},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx.Client, "post", _post)

    document = doc_service.store_upload(
        db,
        content=(FIXTURES / "customer_po_solaris.pdf").read_bytes(),
        filename="po.pdf",
        content_type="application/pdf",
        kind=DocumentKind.UPLOAD_CUSTOMER_PO,
    )
    extraction = doc_service.run_extraction(
        db, document, "customer_po", extractor_name="openai_compatible"
    )
    db.commit()

    assert extraction.status == ExtractionStatus.FAILED
    assert extraction.raw_response == "sorry, I cannot"
    assert extraction.error_text


def test_uploading_the_same_file_twice_stores_one_blob(db):
    from app.models import DocumentKind
    from app.services import documents as doc_service

    content = (FIXTURES / "customer_po_solaris.pdf").read_bytes()
    first = doc_service.store_upload(
        db, content=content, filename="a.pdf", content_type="application/pdf",
        kind=DocumentKind.UPLOAD_CUSTOMER_PO,
    )
    second = doc_service.store_upload(
        db, content=content, filename="b.pdf", content_type="application/pdf",
        kind=DocumentKind.UPLOAD_CUSTOMER_PO,
    )
    assert first.id == second.id
