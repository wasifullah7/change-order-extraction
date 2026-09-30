import re
from datetime import date
from difflib import SequenceMatcher

from .schema import (
    AUTO_APPROVE_MIN_CONFIDENCE,
    CRITICAL_FIELDS,
    GROUNDING_MIN_RATIO,
    MONEY_TOL,
    ChangeOrder,
    Flag,
    Scores,
    cited_fields,
)

DISAGREEMENT_PENALTY = 0.6
UNGROUNDED_CEILING = 0.45
# Values this short match almost any page by accident, so looking for them
# proves nothing. Agreement and the arithmetic checks cover these instead.
MIN_CHECKABLE_LEN = 3


def normalise(text) -> str:
    """Lowercase, collapse whitespace, drop thousands separators.

    Separators go so that "8,417,484.00" and "8417484.00" compare as the same
    span, whichever way the model chose to copy it.
    """
    flat = re.sub(r"\s+", " ", str(text or "")).strip().lower()
    return re.sub(r"(?<=\d),(?=\d)", "", flat)


def evidence_ratio(evidence: str, page: str) -> float:
    """How much of the evidence span appears on the page. Both pre-normalised."""
    if not evidence:
        return 0.0
    if evidence in page:
        return 1.0
    match = SequenceMatcher(None, evidence, page, autojunk=False).find_longest_match(
        0, len(evidence), 0, len(page)
    )
    return match.size / len(evidence)


def _date_spellings(value: str) -> set[str]:
    """Every common way a date gets printed, or empty if it is not a date.

    Dates are asked for in ISO but almost never printed that way, so a literal
    search for the ISO form would flag correct readings as invented.
    """
    try:
        d = date.fromisoformat(str(value).strip())
    except ValueError:
        return set()
    return {
        d.isoformat(),
        f"{d.month}/{d.day}/{d.year}",
        f"{d.month:02d}/{d.day:02d}/{d.year}",
        f"{d.day}/{d.month}/{d.year}",
        f"{d.day:02d}/{d.month:02d}/{d.year}",
        f"{d.month}-{d.day}-{d.year}",
        f"{d.month:02d}-{d.day:02d}-{d.year}",
        f"{d:%B} {d.day}, {d.year}".lower(),
        f"{d:%b} {d.day}, {d.year}".lower(),
        f"{d.day} {d:%B} {d.year}".lower(),
    }


def value_on_page(value, page: str) -> bool:

    if value is None:
        return True

    if isinstance(value, (int, float)):
        candidates = {str(value), f"{float(value):.2f}"}
        if float(value).is_integer():
            candidates.add(str(int(value)))
    else:
        candidates = _date_spellings(value) or {normalise(value)}

    if min(len(c) for c in candidates) < MIN_CHECKABLE_LEN:
        return True
    return any(c in page for c in candidates)


def ungrounded(value, evidence: str, page: str) -> str | None:
    """Why this value cannot be trusted against the page, or None if it can."""
    if not page:
        return None
    ratio = evidence_ratio(normalise(evidence), page)
    if ratio < GROUNDING_MIN_RATIO:
        return f"evidence matches the page at only {ratio:.0%}"
    if not value_on_page(value, page):
        return f"value {value!r} does not appear on the page"
    return None


def same_value(a, b) -> bool:
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(float(a) - float(b)) <= MONEY_TOL
    return normalise(a) == normalise(b)


def disagreements(a: ChangeOrder, b: ChangeOrder) -> list[str]:
    """Fields two extractions of the same document read differently."""
    peers = cited_fields(b)
    out = [
        f"{name}: {field.value!r} vs {peers[name].value!r}"
        for name, field in cited_fields(a).items()
        if name in peers and not same_value(field.value, peers[name].value)
    ]
    if len(a.line_items) != len(b.line_items):
        out.append(f"line_items: {len(a.line_items)} rows vs {len(b.line_items)} rows")
    return out


def _flag(field: str, message: str) -> Flag:
    return Flag(code="UNGROUNDED_VALUE", severity="warning", field=field, message=message)


def score(co: ChangeOrder, source_text: str, other: ChangeOrder | None = None) -> tuple[list[Flag], Scores]:
    flags: list[Flag] = []
    page = normalise(source_text)
    fields = cited_fields(co)
    peers = cited_fields(other) if other else {}

    for name, field in fields.items():
        if field.value is None:
            continue

        problem = ungrounded(field.value, field.evidence, page)
        if problem:
            field.confidence = min(field.confidence, UNGROUNDED_CEILING)
            flags.append(_flag(name, problem))

        peer = peers.get(name)
        if peer is not None and not same_value(field.value, peer.value):
            field.confidence = round(min(field.confidence, peer.confidence) * DISAGREEMENT_PENALTY, 3)
            flags.append(
                Flag(
                    code="PASS_DISAGREEMENT",
                    severity="warning",
                    field=name,
                    message=f"first pass read {field.value!r}, second pass read {peer.value!r}",
                )
            )

    for i, row in enumerate(co.line_items):
        problem = ungrounded(row.extended_price, row.evidence, page)
        if problem:
            row.confidence = min(row.confidence, UNGROUNDED_CEILING)
            flags.append(_flag(f"line_items[{i}]", problem))

    # Missing fields are abstentions, not errors, so they stay out of the
    # confidence figure and are counted as coverage instead. Absent required
    # fields still force a review via MISSING_FIELD in validate.
    found = {name: f.confidence for name, f in fields.items() if f.value is not None}
    critical_missing = sorted(CRITICAL_FIELDS - found.keys())

    all_conf = list(found.values()) + [r.confidence for r in co.line_items]
    critical_conf = [c for name, c in found.items() if name in CRITICAL_FIELDS]

    flags += [
        Flag(
            code="LOW_CONFIDENCE_FIELD",
            severity="info",
            field=name,
            message=f"read at {conf:.2f}, below the {AUTO_APPROVE_MIN_CONFIDENCE} bar but not a critical field",
        )
        for name, conf in found.items()
        if name not in CRITICAL_FIELDS and conf < AUTO_APPROVE_MIN_CONFIDENCE
    ]

    scores = Scores(
        min_confidence=round(min(all_conf), 3) if all_conf else 0.0,
        min_confidence_critical=round(min(critical_conf), 3) if critical_conf else 0.0,
        fields_found=len(found),
        fields_expected=len(fields),
        critical_missing=critical_missing,
    )
    return flags, scores
