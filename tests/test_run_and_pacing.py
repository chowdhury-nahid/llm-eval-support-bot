"""Tests for the pacing delay between live calls and for repeated runs that report a pass rate."""
import json
from types import SimpleNamespace

import pytest

from evalbot import providers, run
from evalbot.cases import CASES, Case
from evalbot.providers import DEFAULT_DELAY_SECONDS, Provider, ProviderError, get_provider


class FakeClock:
    """A clock the test controls, so pacing is checked without real waiting."""

    def __init__(self):
        self.now = 100.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class Echo(Provider):
    name = "echo"

    def _call(self, model, system, user, temperature):
        return "ok"


@pytest.fixture
def clock(monkeypatch):
    fake = FakeClock()
    monkeypatch.setattr(providers, "time", SimpleNamespace(monotonic=fake.monotonic, sleep=fake.sleep))
    return fake


# --- pacing ---------------------------------------------------------------------------------------

def test_second_call_waits_for_the_full_interval(clock):
    p = Echo(["m"], min_interval=2.0)
    p.chat("s", "u")
    p.chat("s", "u")
    assert clock.sleeps == [2.0]


def test_time_already_spent_counts_towards_the_interval(clock):
    p = Echo(["m"], min_interval=2.0)
    p.chat("s", "u")
    clock.now += 0.5
    p.chat("s", "u")
    assert clock.sleeps == [1.5]


def test_no_wait_before_the_first_call_or_when_interval_is_zero(clock):
    Echo(["m"], min_interval=2.0).chat("s", "u")
    assert clock.sleeps == []
    quiet = Echo(["m"], min_interval=0.0)
    quiet.chat("s", "u")
    quiet.chat("s", "u")
    assert clock.sleeps == []


def test_a_failed_call_still_counts_for_pacing(clock):
    class Fails(Provider):
        name = "fails"

        def _call(self, model, system, user, temperature):
            raise ProviderError("boom")

    p = Fails(["m"], min_interval=1.0)
    for _ in range(2):
        with pytest.raises(ProviderError):
            p.chat("s", "u")
    assert clock.sleeps == [1.0]


def test_live_providers_default_to_a_small_delay_and_offline_to_none(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "x")
    monkeypatch.delenv("EVAL_DELAY_SECONDS", raising=False)
    assert get_provider("groq").min_interval == DEFAULT_DELAY_SECONDS == 1.0
    assert get_provider("offline").min_interval == 0.0


def test_delay_can_be_set_by_argument_or_environment(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "x")
    monkeypatch.setenv("EVAL_DELAY_SECONDS", "0.25")
    assert get_provider("groq").min_interval == 0.25
    assert get_provider("groq", delay=3).min_interval == 3.0  # the argument wins


@pytest.mark.parametrize("bad", ["soon", "-1"])
def test_bad_delay_is_a_clear_error(monkeypatch, bad):
    monkeypatch.setenv("GROQ_API_KEY", "x")
    monkeypatch.setenv("EVAL_DELAY_SECONDS", bad)
    with pytest.raises(ProviderError, match="delay"):
        get_provider("groq")


# --- repeated runs --------------------------------------------------------------------------------

CASE = Case("X1", "faithfulness", ["How many notebooks?"], "states", {"must": [r"\b5\b"], "forbid": [r"unlimited"]})


class StubBot:
    """Returns scripted replies in order. An exception in the script is raised as a provider failure."""

    def __init__(self, replies):
        self.replies = iter(replies)

    def answer(self, question):
        reply = next(self.replies)
        if isinstance(reply, Exception):
            raise reply
        return reply, []


def test_repeat_one_is_the_plain_single_run():
    result = run.run_case_repeated(StubBot(["5 notebooks"]), CASE, 1)
    assert result["passed"] is True and "repeat" not in result


def test_intermittent_failure_shows_up_as_a_rate():
    result = run.run_case_repeated(StubBot(["5 notebooks", "unlimited notebooks", "5 notebooks"]), CASE, 3)
    assert (result["passes"], result["scored_runs"]) == (2, 3)
    assert result["rate"] == pytest.approx(2 / 3)
    assert result["passed"] is False
    assert "failed 1 of 3 runs" in result["reason"]
    assert [r["passed"] for r in result["runs"]] == [True, False, True]
    assert "unlimited" in result["answers"][0]  # the report shows the failing answer


def test_steady_pass_and_steady_fail():
    ok = run.run_case_repeated(StubBot(["5"] * 3), CASE, 3)
    assert ok["rate"] == 1.0 and ok["passed"] is True and "failed" not in ok["reason"]
    bad = run.run_case_repeated(StubBot(["unlimited"] * 3), CASE, 3)
    assert bad["rate"] == 0.0 and bad["passed"] is False


def test_provider_errors_are_not_counted_as_runs():
    mixed = run.run_case_repeated(StubBot([ProviderError("x"), "5", "5"]), CASE, 3)
    assert (mixed["passes"], mixed["scored_runs"], mixed["rate"], mixed["passed"]) == (2, 2, 1.0, True)
    none = run.run_case_repeated(StubBot([ProviderError("x")] * 3), CASE, 3)
    assert none["rate"] is None and none["passed"] is None


def test_report_has_a_repeat_table_that_marks_intermittent_cases():
    results = [run.run_case_repeated(StubBot(["5", "unlimited", "5"]), CASE, 3)]
    text = run.render(results, "groq", "m")
    assert "## Repeat runs" in text
    assert "| X1 | faithfulness | 2 of 3 | 67% | intermittent |" in text
    assert "not as a measured failure rate" in text


def test_report_without_repeats_is_unchanged():
    text = run.render([run.run_case_repeated(StubBot(["5"]), CASE, 1)], "groq", "m")
    assert "Repeat runs" not in text


# --- selecting cases and the command line ---------------------------------------------------------

def test_select_cases():
    assert len(run.select_cases(None)) == len(CASES) == 20
    injection = run.select_cases(["prompt-injection"])
    assert len(injection) == 4 and {c.failure_type for c in injection} == {"prompt-injection"}
    with pytest.raises(ValueError, match="unknown failure type"):
        run.select_cases(["nope"])


def test_command_line_repeats_and_writes_a_report(tmp_path, monkeypatch):
    monkeypatch.setattr(run, "REPORTS", tmp_path)
    assert run.main(["--provider", "offline", "--types", "prompt-injection", "--repeat", "3"]) == 0
    data = json.loads(next(tmp_path.glob("*.json")).read_text(encoding="utf-8"))
    assert len(data) == 4 and all(r["repeat"] == 3 and len(r["runs"]) == 3 for r in data)
    assert "## Repeat runs" in next(tmp_path.glob("*.md")).read_text(encoding="utf-8")


def test_repeat_can_come_from_the_environment(tmp_path, monkeypatch):
    monkeypatch.setattr(run, "REPORTS", tmp_path)
    monkeypatch.setenv("EVAL_REPEAT", "2")
    monkeypatch.setenv("EVAL_TYPES", "hallucination")
    run.main(["--provider", "offline"])
    data = json.loads(next(tmp_path.glob("*.json")).read_text(encoding="utf-8"))
    assert len(data) == 4 and all(r["repeat"] == 2 for r in data)


@pytest.mark.parametrize("args", [["--repeat", "0"], ["--repeat", "21"], ["--types", "nope"]])
def test_bad_arguments_are_rejected(args, tmp_path, monkeypatch):
    monkeypatch.setattr(run, "REPORTS", tmp_path)
    with pytest.raises(SystemExit):
        run.main(["--provider", "offline", *args])
