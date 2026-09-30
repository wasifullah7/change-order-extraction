import math
from datetime import date

from .schema import MONEY_TOL, REQUIRED, ChangeOrder, Flag, cited_fields

MAX_PLAUSIBLE_MONEY = 100_000_000.0


def money_equal(a: float | None, b: float | None) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return math.isclose(a, b, rel_tol=0.0, abs_tol=MONEY_TOL)


def _parse_date(raw: str | None) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(raw.strip())
    except ValueError:
        return None


def _rows(co: ChangeOrder) -> list[Flag]:
    flags = []
    for i, row in enumerate(co.line_items):
        where = f"line_items[{i}]"
        if row.quantity is not None and row.unit_price is not None:
            computed = round(row.quantity * row.unit_price, 2)
            if row.extended_price is not None and not money_equal(computed, row.extended_price):
                flags.append(
                    Flag(
                        code="ROW_EXTENDED_MISMATCH",
                        severity="error",
                        field=f"{where}.extended_price",
                        message=f"{row.quantity} x {row.unit_price} is {computed}, page says {row.extended_price}",
                        expected=computed,
                        actual=row.extended_price,
                    )
                )
        for name in ("quantity", "unit_price", "extended_price"):
            v = getattr(row, name)
            if v is not None and (v < 0 or v > MAX_PLAUSIBLE_MONEY):
                flags.append(
                    Flag(
                        code="SUSPECT_VALUE",
                        severity="warning",
                        field=f"{where}.{name}",
                        message=f"{name} of {v} is outside the plausible range",
                        actual=v,
                    )
                )
    return flags


def _mismatch(code: str, field: str, message: str, expected: float, actual: float | None) -> Flag:
    return Flag(
        code=code, severity="error", field=field, message=message, expected=expected, actual=actual
    )


def _totals(co: ChangeOrder) -> list[Flag]:
    flags = []
    t = co.totals
    extendeds = [r.extended_price for r in co.line_items if r.extended_price is not None]

    if extendeds:
        summed = round(sum(extendeds), 2)
        if t.subtotal.value is not None and not money_equal(summed, t.subtotal.value):
            flags.append(
                _mismatch(
                    "SUBTOTAL_MISMATCH",
                    "totals.subtotal",
                    f"line items sum to {summed}, page says {t.subtotal.value}",
                    summed,
                    t.subtotal.value,
                )
            )

    if t.subtotal.value is not None and t.markup_pct.value:
        computed = round(t.subtotal.value * t.markup_pct.value / 100, 2)
        if t.markup.value is not None and not money_equal(computed, t.markup.value):
            flags.append(
                _mismatch(
                    "MARKUP_MISMATCH",
                    "totals.markup",
                    f"{t.markup_pct.value}% of {t.subtotal.value} is {computed}, page says {t.markup.value}",
                    computed,
                    t.markup.value,
                )
            )

    if t.subtotal.value is not None:
        parts = [t.subtotal.value, t.tax.value, t.markup.value]
        computed = round(sum(p for p in parts if p is not None), 2)
        if t.total.value is not None and not money_equal(computed, t.total.value):
            flags.append(
                _mismatch(
                    "TOTAL_MISMATCH",
                    "totals.total",
                    f"subtotal + tax + markup is {computed}, page says {t.total.value}",
                    computed,
                    t.total.value,
                )
            )
    return flags


def _fields(co: ChangeOrder) -> list[Flag]:
    fields = cited_fields(co)
    flags = [
        Flag(
            code="MISSING_FIELD",
            severity=severity,
            field=name,
            message=f"{name} not found on the document",
        )
        for name, severity in REQUIRED.items()
        if not fields[name].value
    ]
    if not co.line_items:
        flags.append(
            Flag(
                code="NO_LINE_ITEMS",
                severity="warning",
                field="line_items",
                message="no line items extracted",
            )
        )
    return flags


def _dates(co: ChangeOrder) -> list[Flag]:
    flags = []
    issued = _parse_date(co.co_date.value)
    if co.co_date.value and issued is None:
        flags.append(
            Flag(
                code="BAD_DATE",
                severity="warning",
                field="co_date",
                message=f"{co.co_date.value!r} is not an ISO date",
            )
        )
    signatures = {
        "contractor_signed_date": co.contractor_signed_date.value,
        "owner_signed_date": co.owner_signed_date.value,
        "architect_signed_date": co.architect_signed_date.value,
    }
    for name, raw in signatures.items():
        signed = _parse_date(raw)
        if raw and signed is None:
            flags.append(
                Flag(code="BAD_DATE", severity="warning", field=name, message=f"{raw!r} is not an ISO date")
            )
        elif signed and issued and signed < issued:
            flags.append(
                Flag(
                    code="DATE_ORDER",
                    severity="warning",
                    field=name,
                    message=f"signed {signed}, before the change order date {issued}",
                )
            )
    return flags


def validate(co: ChangeOrder) -> list[Flag]:
    return _fields(co) + _rows(co) + _totals(co) + _dates(co)


def has_errors(flags: list[Flag]) -> bool:
    return any(f.severity == "error" for f in flags)
