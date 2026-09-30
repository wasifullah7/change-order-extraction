from typing import Annotated, Generic, Literal, TypeVar

from pydantic import AfterValidator, BaseModel, Field, computed_field

T = TypeVar("T")

AUTO_APPROVE_MIN_CONFIDENCE = 0.90
GROUNDING_MIN_RATIO = 0.70
# Half a cent each side, so a legitimately rounded line never trips a flag.
# Shared by the arithmetic checks and by cross-pass agreement, which have to
# agree on what the same number means.
MONEY_TOL = 0.005
# Document confidence is the lowest confidence among these, not among every
# field. A weakly read architect name should not sink a document whose money
# reads cleanly; a shaky total should sink it even if everything else is fine.
CRITICAL_FIELDS = frozenset(
    {
        "co_number",
        "co_date",
        "contractor",
        "schedule_impact_days",
        "totals.subtotal",
        "totals.markup",
        "totals.total",
    }
)

# Dotted names resolve through the nested models, same convention as
# CRITICAL_FIELDS and the field paths reported on flags.
REQUIRED = {
    "co_number": "error",
    "co_date": "error",
    "totals.total": "error",
    "project_name": "warning",
    "project_number": "warning",
    "contractor": "warning",
}


def _to_unit(value: float) -> float:
    return min(max(value, 0.0), 1.0)

# Models occasionally return 1.2 or -0.1. Clamping at the boundary keeps a
# sloppy number from becoming a parse failure that loses the whole document.
Confidence = Annotated[float, AfterValidator(_to_unit)]


class Cited(BaseModel, Generic[T]):
    value: T | None = None
    confidence: Confidence
    evidence: str = Field(description="Text copied verbatim from the page")


class LineItem(BaseModel):
    description: str
    quantity: float | None = None
    unit: str | None = None
    unit_price: float | None = None
    extended_price: float | None = None
    confidence: Confidence
    evidence: str


class Totals(BaseModel):
    subtotal: Cited[float]
    tax: Cited[float]
    markup_pct: Cited[float]
    markup: Cited[float]
    total: Cited[float]
    revised_contract_sum: Cited[float]


class ChangeOrder(BaseModel):
    co_number: Cited[str]
    co_date: Cited[str]
    title: Cited[str]
    project_name: Cited[str]
    project_number: Cited[str]
    contract_number: Cited[str]
    contractor: Cited[str]
    owner: Cited[str]
    architect: Cited[str]
    schedule_impact_days: Cited[int]
    line_items: list[LineItem]
    totals: Totals
    contractor_signed_date: Cited[str]
    owner_signed_date: Cited[str]
    architect_signed_date: Cited[str]


def cited_fields(co: ChangeOrder) -> dict[str, Cited]:
    """Every Cited on the document, keyed by the dotted path used in flags."""
    found = {
        name: getattr(co, name)
        for name in ChangeOrder.model_fields
        if isinstance(getattr(co, name), Cited)
    }
    for name in Totals.model_fields:
        found[f"totals.{name}"] = getattr(co.totals, name)
    return found


Severity = Literal["error", "warning", "info"]


class Flag(BaseModel):
    code: str
    severity: Severity
    field: str
    message: str
    expected: float | None = None
    actual: float | None = None


class SourceInfo(BaseModel):
    path: str
    pages: int
    mode: Literal["text", "vision"]
    reason: str


class Scores(BaseModel):

    min_confidence: float
    min_confidence_critical: float
    fields_found: int
    fields_expected: int
    critical_missing: list[str]

    @computed_field
    @property
    def coverage(self) -> float:
        if not self.fields_expected:
            return 0.0
        return round(self.fields_found / self.fields_expected, 3)

    @computed_field
    @property
    def coverage_critical(self) -> float:
        return round(1 - len(self.critical_missing) / len(CRITICAL_FIELDS), 3)


class ExtractionResult(BaseModel):
    source: SourceInfo
    model: str
    change_order: ChangeOrder
    flags: list[Flag]
    scores: Scores
    routing: Literal["auto_approve", "review"]
