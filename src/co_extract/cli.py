import argparse
import sys
from pathlib import Path

import anthropic

from . import confidence, extract, pdf, validate
from .schema import AUTO_APPROVE_MIN_CONFIDENCE, ExtractionResult, SourceInfo


def process(
    path: str,
    model: str,
    verify_model: str | None = None,
    client: anthropic.Anthropic | None = None,
    doc: pdf.Document | None = None,
) -> ExtractionResult:
    doc = doc or pdf.load(path)
    co = extract.run_pass(doc, model, client)
    second = extract.run_pass(doc, verify_model, client) if verify_model else None

    conf_flags, scores = confidence.score(co, doc.source_text, second)
    flags = validate.validate(co) + conf_flags


    # Three independent gates: any arithmetic error, any shaky critical field,
    # or any critical field never found at all sends the document to review.
    routing = (
        "review"
        # A scan never auto-approves. With no text layer there is nothing to
        # ground values against, and a model that quietly reconciles the
        # arithmetic defeats the other check too. A hand-filled test form
        # produced exactly that: a misread quantity and a corrected total,
        # both reported at 0.95.
        if doc.mode == "vision"
        or validate.has_errors(flags)
        or scores.min_confidence_critical < AUTO_APPROVE_MIN_CONFIDENCE
        or scores.critical_missing
        else "auto_approve"
    )
    return ExtractionResult(
        source=SourceInfo(path=doc.path, pages=len(doc.pages), mode=doc.mode, reason=doc.reason),
        model=f"{model}+{verify_model}" if verify_model else model,
        change_order=co,
        flags=flags,
        scores=scores,
        routing=routing,
    )


def summarise(result: ExtractionResult) -> None:
    co, s = result.change_order, result.scores
    print(f"{result.source.path}  [{result.source.mode}: {result.source.reason}]")
    print(f"  CO {co.co_number.value or '?'}  {co.title.value or ''}")
    print(f"  total {co.totals.total.value}  schedule {co.schedule_impact_days.value} days")
    print(
        f"  {len(co.line_items)} line items, "
        f"coverage {s.fields_found}/{s.fields_expected} ({s.coverage:.0%})"
    )
    print(
        f"  confidence: critical {s.min_confidence_critical}, all fields {s.min_confidence}"
        f"   critical coverage {s.coverage_critical:.0%}"
    )
    if s.critical_missing:
        print(f"  critical fields not found: {', '.join(s.critical_missing)}")
    if result.flags:
        print(f"  {len(result.flags)} flags:")
        for f in result.flags:
            print(f"    [{f.severity}] {f.code} {f.field}: {f.message}")
    else:
        print("  no flags")
    print(f"  -> {result.routing}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="co-extract")
    parser.add_argument("pdf", help="path to a change order PDF")
    parser.add_argument("--model", default=extract.FIRST_PASS_MODEL)
    parser.add_argument(
        "--verify",
        nargs="?",
        const=extract.SECOND_PASS_MODEL,
        default=None,
        metavar="MODEL",
        help="run a second pass and flag any field the two passes disagree on",
    )
    parser.add_argument("--out", help="write the result JSON here")
    args = parser.parse_args(argv)

    if not Path(args.pdf).is_file():
        parser.error(f"no such file: {args.pdf}")

    result = process(args.pdf, args.model, args.verify)
    summarise(result)

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(result.model_dump_json(indent=2), encoding="utf-8")
        print(f"  wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
