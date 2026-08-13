# LLM Eval Capsule

English | [简体中文](README.md)

[![CI](https://github.com/MikeJack302/llm-eval-capsule/actions/workflows/ci.yml/badge.svg)](https://github.com/MikeJack302/llm-eval-capsule/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-3776AB.svg)](https://www.python.org/)

A zero-runtime-dependency CLI for graduate researchers and AI labs. Before reporting that a model improved by 3%, audit its model revision, sampling controls, evaluation data, prompts, judges, rubrics, environment, and metric evidence—then bind the entire record into a SHA-256 reproducibility capsule.

It does not run models and is not tied to one evaluation framework. Evidence can come from LightEval, lm-evaluation-harness, custom scripts, or API experiments.

## Problems it catches

- A hosted model kept the same name while its underlying revision changed.
- Sampling used `temperature > 0`, but no seed was recorded.
- A test set has a name but no split, revision, license, or contamination assessment.
- The evaluated model also serves as its own judge.
- A paper table reports metrics without per-example outputs or scoring evidence.
- Prompts, rubrics, or dependencies changed after the run.
- An `.env`, private key, or credential file was accidentally declared as an artifact.

## Five commands

```text
audit    Check a study specification and all referenced files
capture  Bind the full policy, audit result, and file hashes into a capsule
verify   Detect changes to capsule metadata or artifacts
diff     Separate intended model comparisons from experimental drift
report   Render a Chinese or English Markdown appendix record
```

## Quick start

Python 3.10+ is required. The installed runtime uses only the standard library.

```powershell
git clone https://github.com/MikeJack302/llm-eval-capsule.git
cd llm-eval-capsule
python -m pip install .

llm-eval-capsule audit examples/mini-study/study.json `
  --root examples/mini-study `
  --policy examples/mini-study/policy.json

llm-eval-capsule capture examples/mini-study/study.json `
  --root examples/mini-study `
  --policy examples/mini-study/policy.json `
  -o capsule.json

llm-eval-capsule verify capsule.json --root examples/mini-study
llm-eval-capsule report capsule.json --language en -o report.md
```

WSL2 / Linux:

```bash
llm-eval-capsule audit examples/mini-study/study.json \
  --root examples/mini-study \
  --policy examples/mini-study/policy.json
```

Run directly from a clone:

```powershell
$env:PYTHONPATH = "src"
python -m llm_eval_capsule --help
```

## Study specification

See [`examples/mini-study/study.json`](examples/mini-study/study.json) for a complete example. The core structure is:

```json
{
  "schema": "llm-eval-capsule.study/v1",
  "study_id": "thesis-eval-2026-08",
  "title": "Instruction-following pilot",
  "task": "structured instruction following",
  "hypothesis": "Candidate exceeds 0.70 exact match.",
  "models": [
    {
      "id": "candidate",
      "role": "system-under-test",
      "provider": "example-cloud",
      "name": "research-llm-small",
      "revision": "model-2026-08-01",
      "parameters": {"temperature": 0.2, "seed": 20260813}
    }
  ],
  "datasets": [],
  "prompts": [],
  "evaluators": [],
  "metrics": [],
  "environment_files": [],
  "artifacts": [],
  "declarations": {}
}
```

Every `path` is relative to `--root`. One file may have multiple evidence roles: per-example output can be both `metric-evidence` and `raw-output`. The capsule hashes it once and merges its roles.

## Policy checks

The default policy covers:

| Category | Checks |
|---|---|
| Study design | Study ID, task, and a falsifiable hypothesis |
| Models | Provider, name, immutable revision, parameters, sampling seed, unique IDs |
| Data | Immutable revision, split, license, contamination assessment, PII, and consent |
| Prompts | Version-controlled prompt files |
| Evaluators | rule/model/human kind, rubric, judge reference, and self-judge risk |
| Human review | Blinding; optional inter-rater method requirement |
| Metrics | Value, sample count, and per-example evidence |
| Environment | A lockfile or environment description |
| File safety | Missing files, absolute paths, traversal, size limits, and secret-like names |

Override the policy with JSON; see [`examples/mini-study/policy.json`](examples/mini-study/policy.json). Stable exit codes make `audit` and `capture` suitable for CI.

## Integrity model

`capture` computes SHA-256 over canonical JSON that binds:

- the complete study specification;
- the expanded effective policy, not only user overrides;
- the audit pass state and every finding;
- each referenced file's path, roles, byte count, and SHA-256.

`captured_at` is excluded from the ID, so identical content captured at different times has the same capsule ID. A change to the policy, result, model configuration, or any evidence file is detectable by `verify`.

> SHA-256 is an integrity digest, not a digital signature. It detects changes but does not identify the capsule author. Use signed Git commits, Sigstore, or an institutional archive to sign `capsule.json` when identity assurance matters.

## Drift detection

Require identical conditions for a strict rerun:

```powershell
llm-eval-capsule diff baseline.json rerun.json --mode exact
```

When comparing candidate models, allow the system under test to change while holding the dataset, prompt, judge, rubric, policy, and design constant:

```powershell
llm-eval-capsule diff model-a.json model-b.json --mode model-comparison
```

| Change | `exact` | `model-comparison` |
|---|---:|---:|
| System under test | error | info (expected) |
| Judge / support model | error | error |
| Data, prompt, rubric, policy, design | error | error |
| Environment | warning | warning |
| Metric value | info | info |

## Exit codes

| Code | Meaning |
|---:|---|
| `0` | Audit/verification passed, or diff has no error |
| `2` | Policy failure, integrity failure, or breaking drift |
| `3` | Missing, invalid, or unsupported JSON/arguments |

`audit`, `verify`, and `diff` support `--format json`.

## Research boundaries

- A seed can be best-effort for hosted APIs; recording it does not guarantee token-level determinism.
- Contamination status is a researcher declaration. The tool checks that it was documented, not that training overlap is impossible.
- A file digest proves bit-level identity, not legality, lack of bias, or scientific validity.
- Model judges can remain systematically biased. Important studies should combine blinded humans, multiple judges, or rule-based metrics.
- Do not publish private raw data. The default policy rejects declared PII and secret-like artifact paths.

## Test

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -v
```

CI covers Windows and Ubuntu on Python 3.10 and 3.13.

## Design references

- [Hugging Face Model Cards](https://huggingface.co/docs/hub/model-cards): documenting use, limitations, experimental parameters, datasets, and evaluation results.
- [Hugging Face Dataset Cards](https://huggingface.co/docs/hub/datasets-cards): licenses, context, biases, and responsible use.
- [Hugging Face Evaluate](https://huggingface.co/docs/evaluate/index): evaluation workflows for metrics, measurements, and comparisons.
- [NIST GenAI Evaluation Program](https://ai-challenges.nist.gov/genai): generative-AI test and measurement science.
- [MLCommons MLPerf Endpoints](https://mlcommons.org/benchmarks/endpoints/): an open, fair, and reproducible GenAI endpoint benchmark goal.

## License

MIT
