"""Optional model-graded faithfulness score using DeepEval, judged by the same provider layer.

The deterministic checks decide pass or fail. This adds a second opinion on whether each answer
is supported by the retrieved passages. Free-tier models are weak judges, so treat the score as a
signal to read the answer, not as a verdict.

    python -m evalbot.judge --provider groq
"""
from __future__ import annotations

import argparse

from deepeval.metrics import FaithfulnessMetric
from deepeval.models import DeepEvalBaseLLM
from deepeval.test_case import LLMTestCase

from .bot import SupportBot
from .cases import CASES, FAITHFULNESS
from .providers import PROVIDER_NAMES, Provider, get_provider


class ProviderJudge(DeepEvalBaseLLM):
    def __init__(self, provider: Provider):
        self.provider = provider
        super().__init__(model=provider.name)

    def load_model(self):
        return self.provider

    def generate(self, prompt: str, *args, **kwargs) -> str:
        return self.provider.chat("You are a careful evaluator. Reply with valid JSON only when asked.", prompt)

    async def a_generate(self, prompt: str, *args, **kwargs) -> str:
        return self.generate(prompt)

    def get_model_name(self) -> str:
        return f"{self.provider.name}:{self.provider.last_model or 'unset'}"


def score_faithfulness(provider: Provider) -> list[tuple[str, float | None, str]]:
    bot = SupportBot(provider)
    metric = FaithfulnessMetric(model=ProviderJudge(provider), threshold=0.7, async_mode=False)
    rows = []
    for case in (c for c in CASES if c.failure_type == FAITHFULNESS):
        question = case.questions[0]
        answer, ctx = bot.answer(question)
        tc = LLMTestCase(input=question, actual_output=answer, retrieval_context=[p.text for p in ctx])
        try:
            metric.measure(tc)
            rows.append((case.id, metric.score, metric.reason or ""))
        except Exception as exc:  # a weak judge can return unparsable JSON
            rows.append((case.id, None, f"judge error: {exc}"))
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", choices=[p for p in PROVIDER_NAMES if p != "offline"], required=True)
    args = ap.parse_args(argv)
    for case_id, score, reason in score_faithfulness(get_provider(args.provider)):
        print(f"{case_id}: {score if score is not None else 'n/a'}  {reason[:140]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
