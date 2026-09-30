from contextlib import contextmanager
from pathlib import Path

import pytest

from co_extract import pdf
from co_extract.confidence import score
from co_extract.schema import ChangeOrder, Cited, LineItem, Totals
from co_extract.validate import has_errors, validate

SAMPLES = Path("samples")

PAGE = """Change Order No: CO-014
Date: 2026-03-11
Project: Northgate MOB
Contractor: Bellweather
Rock excavation 48.00 CY 92.50 4,440.00
Subtotal 4,440.00
Overhead & profit (10%) 444.00
TOTAL THIS CHANGE ORDER 4,884.00
The Contract Time will be increased by 7 days."""


def cited(value, confidence=0.98, evidence=None):
    return Cited(value=value, confidence=confidence, evidence=evidence or str(value))


def row(qty=48.0, price=92.50, extended=4440.00, confidence=0.97):
    return LineItem(
        description="Rock excavation",
        quantity=qty,
        unit="CY",
        unit_price=price,
        extended_price=extended,
        confidence=confidence,
        evidence=f"Rock excavation {qty:.2f} CY {price:.2f} {extended:,.2f}",
    )


def change_order(rows=None, subtotal=None, markup=None, total=None, **overrides):
    rows = [row()] if rows is None else rows
    subtotal = round(sum(r.extended_price or 0 for r in rows), 2) if subtotal is None else subtotal
    markup = round(subtotal * 0.10, 2) if markup is None else markup
    total = round(subtotal + markup, 2) if total is None else total

    fields = dict(
        co_number=cited("CO-014", evidence="Change Order No: CO-014"),
        co_date=cited("2026-03-11", evidence="Date: 2026-03-11"),
        title=cited("Rock excavation"),
        project_name=cited("Northgate MOB", evidence="Project: Northgate MOB"),
        project_number=cited("NG-2291"),
        contract_number=cited("C-4417"),
        contractor=cited("Bellweather", evidence="Contractor: Bellweather"),
        owner=cited("Northgate Holdings"),
        architect=cited("Marsh & Doyle"),
        schedule_impact_days=cited(7, evidence="increased by 7 days"),
        line_items=rows,
        totals=Totals(
            subtotal=cited(subtotal, evidence=f"Subtotal {subtotal:,.2f}"),
            tax=cited(0.0, evidence="0.00"),
            markup_pct=cited(10.0, evidence="Overhead & profit (10%)"),
            markup=cited(markup, evidence=f"Overhead & profit (10%) {markup:,.2f}"),
            total=cited(total, evidence=f"TOTAL THIS CHANGE ORDER {total:,.2f}"),
            revised_contract_sum=cited(None, evidence=""),
        ),
        contractor_signed_date=cited("2026-03-12"),
        owner_signed_date=cited("2026-03-16"),
        architect_signed_date=cited("2026-03-13"),
    )
    fields.update(overrides)
    return ChangeOrder(**fields)


def codes(flags):
    return {f.code for f in flags}


def test_consistent_change_order_raises_nothing():
    assert validate(change_order()) == []


def test_extended_price_that_contradicts_quantity_times_rate():
    flags = validate(change_order(rows=[row(qty=18.5, price=214.00, extended=3859.00)]))
    flag = next(f for f in flags if f.code == "ROW_EXTENDED_MISMATCH")
    assert (flag.expected, flag.actual) == (3959.00, 3859.00)
    assert has_errors(flags)


def test_subtotal_that_ignores_the_line_items():
    assert "SUBTOTAL_MISMATCH" in codes(validate(change_order(subtotal=9999.00)))


def test_markup_that_does_not_match_its_percentage():
    assert "MARKUP_MISMATCH" in codes(validate(change_order(markup=1.00)))


def test_total_that_does_not_add_up():
    assert "TOTAL_MISMATCH" in codes(validate(change_order(total=1.00)))


def test_half_cent_rounding_is_not_an_error():
    assert validate(change_order(total=4884.004)) == []


def test_missing_fields_carry_the_right_severity():
    flags = validate(change_order(co_number=cited(None), project_name=cited(None)))
    severities = {f.field: f.severity for f in flags if f.code == "MISSING_FIELD"}
    assert severities["co_number"] == "error"
    assert severities["project_name"] == "warning"


def test_signature_dated_before_the_change_order():
    assert "DATE_ORDER" in codes(validate(change_order(contractor_signed_date=cited("2026-01-01"))))


def test_a_wrong_value_under_a_correct_label_does_not_ground():
    # The label alone matches the page at 91%, so the value is looked for
    # separately. Without that check this is exactly how a bad number hides.
    co = change_order(co_number=cited("CO-099", 0.99, "Change Order No: CO-099"))
    flags, _ = score(co, PAGE)
    assert any(f.code == "UNGROUNDED_VALUE" and f.field == "co_number" for f in flags)
    assert co.co_number.confidence <= 0.45


def test_evidence_that_is_not_on_the_page_does_not_ground():
    co = change_order(co_number=cited("CO-014", 0.99, "Liquidated damages clause 7.4"))
    flags, _ = score(co, PAGE)
    assert any(f.code == "UNGROUNDED_VALUE" and f.field == "co_number" for f in flags)


def test_line_items_are_grounded_the_same_way_fields_are():
    co = change_order(rows=[row(extended=9999.00)])
    flags, _ = score(co, PAGE)
    assert any(f.code == "UNGROUNDED_VALUE" and f.field == "line_items[0]" for f in flags)


def test_disagreement_between_two_passes_lowers_confidence():
    first = change_order()
    second = change_order(co_number=cited("CO-01A", 0.90, "Change Order No: CO-01A"))
    flags, _ = score(first, PAGE, second)
    assert any(f.code == "PASS_DISAGREEMENT" for f in flags)
    assert first.co_number.confidence < 0.9


def test_confidence_is_reported_against_its_coverage():
    # A document that barely read must not look healthy just because the few
    # fields it did find were read confidently.
    blank = {name: cited(None) for name in ("co_number", "co_date", "contractor", "schedule_impact_days")}
    _flags, scores = score(change_order(**blank), PAGE)
    # The totals that did survive were read confidently, so confidence on its
    # own still looks fine. Coverage is the figure that gives the document away.
    assert scores.min_confidence_critical > 0.9
    assert scores.coverage_critical < 0.5
    assert scores.critical_missing == ["co_date", "co_number", "contractor", "schedule_impact_days"]


def test_a_weak_secondary_field_does_not_sink_the_document():
    co = change_order(architect=cited("Marsh & Doyle", 0.31))
    _flags, scores = score(co, PAGE)
    assert scores.min_confidence == 0.31
    assert scores.min_confidence_critical > 0.9


@pytest.mark.skipif(not (SAMPLES / "sample_clean.pdf").exists(), reason="run scripts/make_sample.py")
def test_digital_pdf_uses_the_text_layer():
    doc = pdf.load(str(SAMPLES / "sample_clean.pdf"))
    assert doc.mode == "text"
    assert doc.source_text


@pytest.mark.skipif(not (SAMPLES / "sample_scanned.pdf").exists(), reason="run scripts/make_sample.py")
def test_scanned_pdf_falls_back_to_page_images():
    doc = pdf.load(str(SAMPLES / "sample_scanned.pdf"))
    assert doc.mode == "vision"
    assert doc.images and not doc.source_text


fastapi = pytest.importorskip("fastapi", reason="install with: uv sync --extra api")
from fastapi.testclient import TestClient  # noqa: E402

from co_extract.api import app, get_client  # noqa: E402


class StubClient:
    """Stands in for the Anthropic client so error routing can be tested."""

    def __init__(self, error=None):
        self.error = error

    @property
    def messages(self):
        return self

    def parse(self, **_):
        raise self.error


@contextmanager
def api_client(stub=None):
    """A test client with the model call stubbed out.

    credential_error is cleared inside the context because entering the client
    runs the lifespan, which probes for real credentials and would set it.
    """
    app.dependency_overrides[get_client] = lambda: stub or StubClient()
    try:
        with TestClient(app) as c:
            app.state.credential_error = None
            yield c
    finally:
        app.dependency_overrides.clear()


def test_api_rejects_a_file_that_is_not_a_pdf():
    with api_client() as c:
        r = c.post("/extract", files={"file": ("notes.txt", b"hello", "text/plain")})
    assert r.status_code == 400
    assert "pdf" in r.json()["detail"].lower()


def test_api_rejects_a_corrupt_pdf_as_a_client_error():
    with api_client() as c:
        r = c.post("/extract", files={"file": ("broken.pdf", b"not really a pdf", "application/pdf")})
    assert r.status_code == 400
    assert "could not read that PDF" in r.json()["detail"]


@pytest.mark.skipif(not (SAMPLES / "sample_clean.pdf").exists(), reason="run scripts/make_sample.py")
def test_api_reports_a_model_failure_as_a_gateway_error():
    stub = StubClient(RuntimeError("upstream exploded"))
    with api_client(stub) as c:
        r = c.post(
            "/extract",
            files={"file": ("co.pdf", (SAMPLES / "sample_clean.pdf").read_bytes(), "application/pdf")},
        )
    assert r.status_code == 502
    assert "upstream exploded" in r.json()["detail"]


def test_health_reports_model_access_separately_from_liveness():
    with api_client() as c:
        body = c.get("/health").json()
    assert body["status"] == "ok"
    assert body["model_access"] == "ok"
    assert body["provider"] in {"anthropic", "openrouter"}


@pytest.mark.skipif(not (SAMPLES / "sample_handwritten.pdf").exists(), reason="run scripts/make_sample.py")
def test_a_hand_filled_scan_is_rendered_for_vision():
    doc = pdf.load(str(SAMPLES / "sample_handwritten.pdf"))
    assert doc.mode == "vision"
    assert not doc.source_text
