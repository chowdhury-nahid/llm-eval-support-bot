"""Run every case against a provider and write a markdown report.

    python -m evalbot.run --provider groq
    python -m evalbot.run --provider groq --types prompt-injection --repeat 5

`--repeat N` runs every selected case N times and reports how often each one passed, because one run
cannot show whether a failure is steady or comes and goes. `--types` limits the run to some failure types.
`--delay` sets the pause in seconds between live calls (default 1).
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
from collections import defaultdict
from pathlib import Path

from .bot import SupportBot
from .cases import CASES, FAILURE_TYPES
from .checks import CHECKS
from .providers import PROVIDER_NAMES, ProviderError, get_provider

REPORTS = Path(__file__).resolve().parent.parent / "reports"
MAX_REPEAT = 20  # a guard against an accidental huge run on a free tier


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


def run_case_repeated(bot: SupportBot, case, repeat: int = 1) -> dict:
    """Run one case `repeat` times. With repeat=1 this is run_case unchanged.

    With more runs the case counts as passed only if every scored run passed, and the result carries
    the pass count and rate so an intermittent failure shows up as one.
    """
    if repeat <= 1:
        return run_case(bot, case)
    runs = [run_case(bot, case) for _ in range(repeat)]
    scored = [r for r in runs if r["passed"] is not None]
    passes = sum(1 for r in scored if r["passed"])
    fails = len(scored) - passes
    failing = next((r for r in scored if not r["passed"]), None)
    shown = failing or (scored[0] if scored else runs[0])
    result = dict(shown)
    result["repeat"] = repeat
    result["scored_runs"] = len(scored)
    result["passes"] = passes
    result["rate"] = passes / len(scored) if scored else None
    result["passed"] = None if not scored else fails == 0
    if fails:
        result["reason"] = f"failed {fails} of {len(scored)} runs; first failing run: {shown['reason']}"
    result["runs"] = [{"passed": r["passed"], "reason": r["reason"], "answers": r["answers"]} for r in runs]
    return result


def select_cases(types: list[str] | None) -> list:
    """All cases, or only those of the named failure types."""
    if not types:
        return list(CASES)
    unknown = [t for t in types if t not in FAILURE_TYPES]
    if unknown:
        raise ValueError(f"unknown failure type {unknown}; expected some of {FAILURE_TYPES}")
    return [c for c in CASES if c.failure_type in types]


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
    repeated = [r for r in results if r.get("repeat")]
    if repeated:
        lines += [f"Each case ran {repeated[0]['repeat']} times. A case counts as passed only if it passed every "
                  "scored run. Read the rates below as samples from this one model on this day, not as a "
                  "measured failure rate of the model.", ""]
    lines += ["| Failure type | Passed | Total |", "|---|---|---|"]
    for t, (p, n) in by_type.items():
        lines.append(f"| {t} | {p} | {n} |")
    if repeated:
        lines += ["", "## Repeat runs", "", "| Case | Failure type | Passed runs | Rate | Pattern |", "|---|---|---|---|---|"]
        for r in repeated:
            if r["rate"] is None:
                lines.append(f"| {r['id']} | {r['type']} | 0 of 0 | n/a | not scored |")
                continue
            pattern = "steady pass" if r["rate"] == 1 else "steady fail" if r["rate"] == 0 else "intermittent"
            lines.append(f"| {r['id']} | {r['type']} | {r['passes']} of {r['scored_runs']} | "
                         f"{r['rate']:.0%} | {pattern} |")
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
    ap.add_argument("--repeat", type=int, default=None,
                    help=f"run every selected case this many times (1 to {MAX_REPEAT}); env EVAL_REPEAT")
    ap.add_argument("--types", default=None,
                    help="comma-separated failure types to run; env EVAL_TYPES. One or more of: "
                         + ", ".join(FAILURE_TYPES))
    ap.add_argument("--delay", type=float, default=None,
                    help="seconds to wait between live calls (default 1); env EVAL_DELAY_SECONDS")
    args = ap.parse_args(argv)
    repeat = args.repeat if args.repeat is not None else int(os.environ.get("EVAL_REPEAT") or 1)
    if not 1 <= repeat <= MAX_REPEAT:
        ap.error(f"--repeat must be between 1 and {MAX_REPEAT}")
    raw_types = args.types if args.types is not None else os.environ.get("EVAL_TYPES", "")
    types = [t.strip() for t in raw_types.split(",") if t.strip()]
    try:
        cases = select_cases(types)
        provider = get_provider(args.provider, args.delay)
    except (ValueError, ProviderError) as exc:
        ap.error(str(exc))
    bot = SupportBot(provider)
    results = [run_case_repeated(bot, c, repeat) for c in cases]
    REPORTS.mkdir(exist_ok=True)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d-%H%M")
    (REPORTS / f"{stamp}-{provider.name}.md").write_text(
        render(results, provider.name, provider.last_model or "n/a"), encoding="utf-8")
    (REPORTS / f"{stamp}-{provider.name}.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    passed = sum(1 for r in results if r["passed"] is True)
    failed = [r for r in results if r["passed"] is False]
    unscored = sum(1 for r in results if r["passed"] is None)
    print(f"{passed} passed, {len(failed)} failed, {unscored} not scored, of {len(results)} cases"
          + (f", {repeat} runs each" if repeat > 1 else "") + "; report written to reports/")
    for r in failed:
        print(f"FAILED {r['id']} ({r['type']}): {r['reason']}")
    for r in results:
        if r.get("rate") is not None and 0 < r["rate"] < 1:
            print(f"INTERMITTENT {r['id']} ({r['type']}): passed {r['passes']} of {r['scored_runs']} runs")
    for r in results:
        if r["passed"] is None:
            print(f"NOT SCORED {r['id']}: {r['reason']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
