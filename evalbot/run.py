"""Run every case against a provider and write a markdown report.

    python -m evalbot.run --provider groq
"""
from __future__ import annotations

import argparse
import datetime
import json
from collections import defaultdict
from pathlib import Path

from .bot import SupportBot
from .cases import CASES
from .checks import CHECKS
from .providers import PROVIDER_NAMES, ProviderError, get_provider

REPORTS = Path(__file__).resolve().parent.parent / "reports"


def run_case(bot: SupportBot, case):
    answers, sources = [], []
    for q in case.questions:
        try:
            reply, ctx = bot.answer(q)
        except ProviderError as exc:
            return {"id": case.id, "type": case.failure_type, "passed": None,
                    "reason": f"provider error: {exc}", "answers": answers, "sources": sources}
        answers.append(reply)
        sources.append([p.source for p in ctx])
    passed, reason = CHECKS[case.check](answers, **case.args)
    return {"id": case.id, "type": case.failure_type, "passed": passed, "reason": reason,
            "questions": case.questions, "answers": answers, "sources": sources}


def render(results: list[dict], provider: str, model: str) -> str:
    when = datetime.date.today().isoformat()
    scored = [r for r in results if r["passed"] is not None]
    passed = sum(1 for r in scored if r["passed"])
    by_type = defaultdict(lambda: [0, 0])
    for r in scored:
        by_type[r["type"]][1] += 1
        by_type[r["type"]][0] += 1 if r["passed"] else 0
    lines = [f"# Evaluation report, {when}", "",
             f"Provider: `{provider}`. Model: `{model}`. Cases scored: {len(scored)} of {len(results)}. "
             f"Passed: {passed}.", ""]
    if provider == "offline":
        lines += ["> Offline run: the answers come from a simple extractive stand-in, not a language model. "
                  "It shows how the harness reports results. It says nothing about any real model.", ""]
    lines += ["| Failure type | Passed | Total |", "|---|---|---|"]
    for t, (p, n) in by_type.items():
        lines.append(f"| {t} | {p} | {n} |")
    lines += ["", "## Failures", ""]
    failures = [r for r in results if r["passed"] is False]
    if not failures:
        lines.append("None in this run.")
    for r in failures:
        lines += [f"### {r['id']} ({r['type']})", "", f"- **What broke:** {r['reason']}",
                  f"- **Question:** {r['questions'][0]}",
                  f"- **Retrieved from:** {', '.join(r['sources'][0]) or 'nothing'}",
                  "- **Why:** _fill in after reading the answer_",
                  "- **Severity:** _high / medium / low_",
                  "- **Fix:** _prompt, retrieval or guardrail change_", ""]
    errors = [r for r in results if r["passed"] is None]
    if errors:
        lines += ["## Not scored", ""] + [f"- {r['id']}: {r['reason']}" for r in errors]
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", choices=PROVIDER_NAMES, default=None)
    args = ap.parse_args(argv)
    provider = get_provider(args.provider)
    bot = SupportBot(provider)
    results = [run_case(bot, c) for c in CASES]
    REPORTS.mkdir(exist_ok=True)
    stamp = datetime.date.today().isoformat()
    (REPORTS / f"{stamp}-{provider.name}.md").write_text(
        render(results, provider.name, provider.last_model or "n/a"), encoding="utf-8")
    (REPORTS / f"{stamp}-{provider.name}.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    failed = sum(1 for r in results if r["passed"] is False)
    print(f"{len(results) - failed} of {len(results)} cases did not fail; report written to reports/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
