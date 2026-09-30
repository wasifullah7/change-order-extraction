import os

import anthropic
from dotenv import load_dotenv

load_dotenv()

# OpenRouter exposes an Anthropic-compatible /v1/messages, structured outputs
# included, so the same SDK and the same code path drive both providers. Only
# the credentials and the model names differ.
OPENROUTER_BASE_URL = "https://openrouter.ai/api"

# OpenRouter namespaces its models and writes versions with dots where the
# Anthropic API uses dashes.
OPENROUTER_MODELS = {
    "claude-haiku-4-5": "anthropic/claude-haiku-4.5",
    "claude-opus-5": "anthropic/claude-opus-5",
    "claude-sonnet-5": "anthropic/claude-sonnet-5",
}


def using_openrouter() -> bool:
    """OpenRouter is a local-testing fallback, so a real key always wins."""
    return not os.getenv("ANTHROPIC_API_KEY") and bool(os.getenv("OPENROUTER_API_KEY"))


def provider() -> str:
    return "openrouter" if using_openrouter() else "anthropic"


def build_client() -> anthropic.Anthropic:
    if using_openrouter():
        return anthropic.Anthropic(
            api_key=os.environ["OPENROUTER_API_KEY"], base_url=OPENROUTER_BASE_URL
        )
    return anthropic.Anthropic()


def model_id(model: str) -> str:
    """Translate a model name for whichever provider is in use."""
    if not using_openrouter() or "/" in model:
        return model
    return OPENROUTER_MODELS.get(model, model)


def check_credentials(client: anthropic.Anthropic) -> str | None:
    """Smallest call that proves the credentials work. None means they do.

    A real request rather than a models lookup, because OpenRouter does not
    serve the models endpoint.
    """
    try:
        client.messages.create(
            model=model_id("claude-haiku-4-5"),
            max_tokens=1,
            messages=[{"role": "user", "content": "ok"}],
        )
    except Exception as exc:
        return str(exc)
    return None
