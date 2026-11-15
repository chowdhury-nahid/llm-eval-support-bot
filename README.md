# llm-eval-support-bot

A test suite for a small documentation question-answering bot. It runs 20 cases against any chat model and writes a failure report: what broke, why, how severe, and what to change.

The bot answers questions about a made-up note-taking app called Lumen Notes, using six short documentation pages kept in `evalbot/docs/`. The pages are my own text, so every correct answer is known exactly. That is what makes a wrong answer measurable.

## What it tests

Four cases for each of five failure types.

| Failure type | What a case checks |
|---|---|
| Hallucination | The bot invents a feature, plan or format the docs do not mention |
| Faithfulness | The answer gives a figure that differs from the retrieved passage |
| Refusals | The bot declines off-topic requests and still answers in-scope ones |
| Prompt injection | A direct request, a role-play, a poisoned documentation passage and a social-engineering message each fail to extract planted secrets or change behaviour |
| Consistency | Three phrasings of one question all return the same key fact |

Pass or fail comes from deterministic checks (`evalbot/checks.py`), not from a model grading itself. The checks have their own tests: for every case there is a correct answer that must pass and a wrong answer that must fail.

An optional DeepEval faithfulness score (`python -m evalbot.judge`) adds a model-graded second opinion. Small free-tier models are unreliable judges, so read it as a prompt to look at the answer, not as a verdict.

## Run it

```
pip install -r requirements.txt
python -m pytest -q                       # tests of the checks, no network or key needed
python -m evalbot.run --provider groq     # the 20 cases against a live model
python -m evalbot.run --provider groq --types prompt-injection --repeat 5   # a pass rate, not one sample
```

Three options shape a live run. Each also has an environment variable, and the command line wins.

| Option | Variable | What it does |
|---|---|---|
| `--delay SECONDS` | `EVAL_DELAY_SECONDS` | Pause between live calls so a free tier is not hit in a burst. Default 1. Offline runs never wait. |
| `--repeat N` | `EVAL_REPEAT` | Run every selected case N times (1 to 20) and add a table of how often each case passed. A case counts as passed only if it passed every run. |
| `--types LIST` | `EVAL_TYPES` | Run only these failure types, comma separated: hallucination, faithfulness, refusal, prompt-injection, consistency. |

Providers: `groq`, `mistral`, `gemini` (free tiers), `anthropic` (needs a paid key), and `offline` (no network, for a dry run of the harness).

| Provider | Key variable | Model list variable (first usable model wins) |
|---|---|---|
| groq | `GROQ_API_KEY` | `GROQ_MODEL` |
| mistral | `MISTRAL_API_KEY` | `MISTRAL_MODEL` |
| gemini | `GEMINI_API_KEY` | `GEMINI_MODEL` |
| anthropic | `ANTHROPIC_API_KEY` | `ANTHROPIC_MODEL` |

Free tiers change their model lists often. If a model is refused, the next one in the list is tried, and you can set your own list in the variable.

The `live evaluation` workflow runs the same thing from the Actions tab using repository secrets, and attaches the report as an artifact.

## First results

One run on Groq's free tier (`openai/gpt-oss-120b`): 17 of 20 cases passed. The three failures were a real indirect prompt injection (high), a retrieval miss on a paraphrase (medium) and a bug in one of my checks (fixed). I ran the workflow once more 80 seconds later: 18 of 20 passed, and the injection that failed the first time did not fail the second. Details, causes and fixes are in [FINDINGS.md](FINDINGS.md).

## What this does and does not show

- It shows how to turn five failure types into repeatable, pass-or-fail cases and a readable report.
- It does not give a failure rate yet. The `--repeat` option measures one, but no repeated run has been recorded in this repository, and a rate from one free-tier model on one day would still be a sample.
- It does not rank models. Twenty cases on one small corpus is a demonstration, and a model that passes here may fail on your documentation.
- The retrieval step is a simple keyword match on purpose, so that failures trace to the model's behaviour and not to a search engine.
- Offline runs use a stand-in that copies the top passage. They test the harness, not any model.

## Layout

```
evalbot/bot.py        retrieval and prompt
evalbot/cases.py      the 20 cases
evalbot/checks.py     the pass or fail rules
evalbot/providers.py  one interface over the chat providers
evalbot/run.py        runner and report writer
evalbot/judge.py      optional DeepEval faithfulness score
reports/              one markdown report per live run
tests/                tests for the checks, the bot and the providers
```

Licence: MIT.
