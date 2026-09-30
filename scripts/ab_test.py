

import sys
from pathlib import Path

import anthropic

from co_extract import cli, confidence, extract, pdf
from co_extract.client import build_client

# USD per million tokens, input / output.
PRICES = {
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-opus-5": (5.00, 25.00),
}
MODELS = [extract.FIRST_PASS_MODEL, extract.SECOND_PASS_MODEL]


class Recorder:
    """Passes calls through to the real client and keeps the usage totals."""

    def __init__(self):
        self._client = build_client()
        self.input_tokens = 0
        self.output_tokens = 0
        self.messages = self

    def parse(self, **kwargs):
        response = self._client.messages.parse(**kwargs)
        self.input_tokens += response.usage.input_tokens
        self.output_tokens += response.usage.output_tokens
        return response

    def cost(self, model: str) -> float:
        rate_in, rate_out = PRICES.get(model, (0.0, 0.0))
        return self.input_tokens / 1e6 * rate_in + self.output_tokens / 1e6 * rate_out


def main() -> int:
    docs = sys.argv[1:] or sorted(str(p) for p in Path("samples").glob("*.pdf"))
    if not docs:
        print("no PDFs found; run scripts/make_sample.py first")
        return 1

    grand_total = 0.0
    for path in docs:
        print(f"\n=== {path} ===")
        # Parsed and rendered once, then reused for every model.
        doc = pdf.load(path)
        results = {}

        for model in MODELS:
            rec = Recorder()
            try:
                result = cli.process(path, model, client=rec, doc=doc)
            except Exception as exc:
                print(f"  {model:20s} FAILED: {exc}")
                continue
            spend = rec.cost(model)
            grand_total += spend
            results[model] = result
            s = result.scores
            errors = sum(1 for f in result.flags if f.severity == "error")
            print(
                f"  {model:20s} {doc.mode:6s} total={result.change_order.totals.total.value} "
                f"rows={len(result.change_order.line_items)} conf={s.min_confidence_critical} "
                f"cov={s.coverage:.0%} flags={len(result.flags)} ({errors} error) "
                f"-> {result.routing}  tok={rec.input_tokens}/{rec.output_tokens} ${spend:.4f}"
            )
            for f in result.flags:
                print(f"      [{f.severity}] {f.code} {f.field}")

        if len(results) == 2:
            a, b = (r.change_order for r in results.values())
            diffs = confidence.disagreements(a, b)
            print(f"  disagreements: {len(diffs)}")
            for d in diffs:
                print(f"      {d}")

    print(f"\ntotal spend: ${grand_total:.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
