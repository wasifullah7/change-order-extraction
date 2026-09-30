import anthropic

from .client import build_client, model_id
from .pdf import Document
from .schema import ChangeOrder

FIRST_PASS_MODEL = "claude-haiku-4-5"
SECOND_PASS_MODEL = "claude-opus-5"

SYSTEM = """You read construction change orders and return structured data.

Copy every value exactly as printed. Do not compute, correct, or reconcile
anything: if the page says an extended price that does not equal quantity times
unit price, return what the page says. A separate arithmetic check runs after
you, and it can only catch errors you faithfully reproduce.

For each field give a confidence and the evidence you read it from.

Confidence bands:
  0.95-1.00  printed explicitly and unambiguously labelled
  0.80-0.94  clearly implied by a label or position, not spelled out
  0.60-0.79  inferred from surrounding context
  0.00-0.59  barely legible, handwritten, or a guess

Evidence must be a short span copied verbatim from the page, including the
label where there is one. Never paraphrase it and never write evidence for a
value you did not find.

Totals are easy to confuse on these forms, so be precise:
  subtotal              line items before tax and markup
  markup                overhead and profit on this change order
  total                 the amount of THIS change order alone
  revised_contract_sum  the whole contract after this change order is applied

A line like "the new Contract Amount including this Change Order will be X" is
the revised contract sum, never the total. If the form gives only one figure
for the change order itself, that figure is the total and the subtotal is null.

Dates use ISO format (YYYY-MM-DD). Money and quantities are plain numbers with
no currency symbols or thousands separators. Use null for anything absent
rather than inventing a placeholder."""

PROMPT = "Extract the change order from this document."
SCANNED_NOTE = (
    " These pages are scanned images and some values may be handwritten."
    " Report a digit exactly as it appears even where the row then fails to"
    " multiply out, and lower your confidence on anything you had to squint"
    " at. Never substitute a figure you calculated for one printed on the page."
)
PAGE_MARKER = "--- page {n} ---"


def _content(doc: Document) -> list[dict]:
    if doc.mode == "vision":
        blocks: list[dict] = [
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": img}}
            for img in doc.images
        ]
        blocks.append({"type": "text", "text": PROMPT + SCANNED_NOTE})
        return blocks
    joined = "\n\n".join(
        f"{PAGE_MARKER.format(n=i + 1)}\n{p}" for i, p in enumerate(doc.pages)
    )
    return [{"type": "text", "text": f"{joined}\n\n{PROMPT}"}]


def run_pass(doc: Document, model: str, client: anthropic.Anthropic | None = None) -> ChangeOrder:
    client = client or build_client()
    response = client.messages.parse(
        model=model_id(model),
        max_tokens=16000,
        system=SYSTEM,
        messages=[{"role": "user", "content": _content(doc)}],
        output_format=ChangeOrder,
    )
    if response.parsed_output is None:
        raise RuntimeError(f"{model} returned no parseable output (stop_reason={response.stop_reason})")
    return response.parsed_output
