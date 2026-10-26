"""One small interface over the chat providers the suite can run against.

Free tiers change often, so each provider takes an ordered list of model ids from an
environment variable. A model the account cannot use is skipped and the next one is tried.
"""
from __future__ import annotations

import os
import time

import requests


class ProviderError(RuntimeError):
    pass


# name -> (chat completions url, key variable, model variable, default models)
OPENAI_COMPATIBLE = {
    "groq": (
        "https://api.groq.com/openai/v1/chat/completions",
        "GROQ_API_KEY",
        "GROQ_MODEL",
        "openai/gpt-oss-120b,openai/gpt-oss-20b",
    ),
    "mistral": (
        "https://api.mistral.ai/v1/chat/completions",
        "MISTRAL_API_KEY",
        "MISTRAL_MODEL",
        "ministral-14b-2512,ministral-8b-2512,mistral-small-latest",
    ),
    "gemini": (
        "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
        "GEMINI_API_KEY",
        "GEMINI_MODEL",
        "gemini-2.5-flash,gemini-2.5-flash-lite",
    ),
}
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_DEFAULT_MODELS = "claude-haiku-4-5"

PROVIDER_NAMES = ["groq", "mistral", "gemini", "anthropic", "offline"]


class Provider:
    """Base class. `chat` returns the reply text for one system and one user message."""

    name = "base"

    def __init__(self, models: list[str]):
        self.models = models
        self.last_model = ""
        self._skip: set[str] = set()

    def chat(self, system: str, user: str, temperature: float = 0.0) -> str:
        last_error = "no model available"
        for model in self.models:
            if model in self._skip:
                continue
            try:
                text = self._call(model, system, user, temperature)
            except ProviderError as exc:
                last_error = f"{model}: {exc}"
                continue
            self.last_model = model
            return text
        raise ProviderError(last_error)

    def _call(self, model: str, system: str, user: str, temperature: float) -> str:
        raise NotImplementedError

    def _post(self, model: str, url: str, headers: dict, body: dict) -> dict:
        for attempt in range(4):
            resp = requests.post(url, headers=headers, json=body, timeout=60)
            if resp.status_code == 429:
                time.sleep(2 ** attempt * 3)
                continue
            if resp.status_code in (400, 403, 404):
                # A model this key cannot use: skip it for the rest of the run.
                self._skip.add(model)
                raise ProviderError(f"HTTP {resp.status_code} {resp.text[:120]}")
            if resp.status_code >= 500:
                time.sleep(2 ** attempt)
                continue
            resp.raise_for_status()
            return resp.json()
        raise ProviderError("rate limited or unavailable after retries")


class OpenAICompatible(Provider):
    def __init__(self, name: str, models: list[str], url: str, key: str):
        super().__init__(models)
        self.name, self.url, self.key = name, url, key

    def _call(self, model, system, user, temperature):
        data = self._post(
            model,
            self.url,
            {"Authorization": f"Bearer {self.key}"},
            {
                "model": model,
                "temperature": temperature,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            },
        )
        return (data["choices"][0]["message"]["content"] or "").strip()


class Anthropic(Provider):
    name = "anthropic"

    def __init__(self, models: list[str], key: str):
        super().__init__(models)
        self.key = key

    def _call(self, model, system, user, temperature):
        data = self._post(
            model,
            ANTHROPIC_URL,
            {"x-api-key": self.key, "anthropic-version": "2023-06-01"},
            {
                "model": model,
                "max_tokens": 600,
                "temperature": temperature,
                "system": system,
                "messages": [{"role": "user", "content": user}],
            },
        )
        return "".join(b.get("text", "") for b in data["content"]).strip()


class Offline(Provider):
    """No network. Echoes the best passage, so the harness and report run on a fresh clone."""

    name = "offline"

    def __init__(self):
        super().__init__(["offline-extractive"])

    def _call(self, model, system, user, temperature):
        marker = "Context:\n"
        context = user.split(marker, 1)[1].split("\n\nQuestion:", 1)[0] if marker in user else ""
        first = context.strip().split("\n\n")[0] if context.strip() else ""
        return first or "That is not covered in the documentation."


def get_provider(name: str | None = None) -> Provider:
    name = (name or os.environ.get("EVAL_PROVIDER") or "offline").lower()
    if name == "offline":
        return Offline()
    if name == "anthropic":
        key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not key:
            raise ProviderError("ANTHROPIC_API_KEY is not set")
        models = os.environ.get("ANTHROPIC_MODEL", ANTHROPIC_DEFAULT_MODELS)
        return Anthropic([m.strip() for m in models.split(",") if m.strip()], key)
    if name in OPENAI_COMPATIBLE:
        url, key_var, model_var, default = OPENAI_COMPATIBLE[name]
        key = os.environ.get(key_var, "")
        if not key:
            raise ProviderError(f"{key_var} is not set")
        models = os.environ.get(model_var, default)
        return OpenAICompatible(name, [m.strip() for m in models.split(",") if m.strip()], url, key)
    raise ProviderError(f"unknown provider '{name}', expected one of {PROVIDER_NAMES}")
