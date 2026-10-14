"""Provider adapters for the LLM planner.

An adapter takes (system prompt, user message, JSON schema) and returns the model's JSON text. It
raises ProviderError when the model refused, was blocked, or stopped early. Adapters expose no
tools and no free-form output: the schema is the only thing the model can answer with. Everything
that decides whether the answer is *acceptable* lives in services/llm_planner.py and is the same
for every provider."""

from collections.abc import Callable
from typing import Any

from app.core.config import settings

DEFAULT_MODELS = {"anthropic": "claude-opus-5", "gemini": "gemini-2.5-flash"}
MAX_OUTPUT_TOKENS = 8000

Provider = Callable[[str, str, dict[str, Any]], str]


class ProviderError(Exception):
    """The model refused, was blocked, stopped early, or returned nothing usable."""


def _model(provider: str) -> str:
    return settings.llm_model or DEFAULT_MODELS[provider]


def anthropic_complete(system: str, user: str, schema: dict[str, Any], client: Any = None) -> str:
    import anthropic

    client = client or anthropic.Anthropic(
        api_key=settings.llm_api_key, timeout=settings.llm_timeout_s, max_retries=1
    )
    response = client.messages.create(
        model=_model("anthropic"),
        max_tokens=MAX_OUTPUT_TOKENS,
        system=system,
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema}},
        messages=[{"role": "user", "content": user}],
    )
    if response.stop_reason != "end_turn":  # refusal, max_tokens, ...
        raise ProviderError(f"model stopped with {response.stop_reason!r}")
    text = next((b.text for b in response.content if b.type == "text"), None)
    if not text:
        raise ProviderError("model returned no text")
    return text


def gemini_complete(system: str, user: str, schema: dict[str, Any], client: Any = None) -> str:
    from google import genai
    from google.genai import types

    client = client or genai.Client(
        api_key=settings.llm_api_key,
        http_options=types.HttpOptions(timeout=int(settings.llm_timeout_s * 1000)),  # milliseconds
    )
    response = client.models.generate_content(
        model=_model("gemini"),
        contents=user,
        config=types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json",
            response_json_schema=schema,
            max_output_tokens=MAX_OUTPUT_TOKENS,
        ),
    )
    if response.prompt_feedback and response.prompt_feedback.block_reason:
        raise ProviderError(f"prompt blocked: {response.prompt_feedback.block_reason}")
    if not response.candidates:
        raise ProviderError("model returned no candidates")
    finish = response.candidates[0].finish_reason
    if finish != types.FinishReason.STOP:  # SAFETY, MAX_TOKENS, PROHIBITED_CONTENT, ...
        raise ProviderError(f"model stopped with {getattr(finish, 'name', finish)}")
    if not response.text:
        raise ProviderError("model returned no text")
    return response.text


PROVIDERS: dict[str, Provider] = {"anthropic": anthropic_complete, "gemini": gemini_complete}


def get_provider() -> Provider:
    name = settings.llm_provider.strip().lower()
    try:
        return PROVIDERS[name]
    except KeyError:
        raise ProviderError(f"unknown LLM provider {name!r}; expected one of {sorted(PROVIDERS)}") from None
