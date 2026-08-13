"""Core audit, capture, integrity, and drift operations."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


class CapsuleError(ValueError):
    """Raised for invalid inputs that cannot be audited safely."""


@dataclass(frozen=True)
class Finding:
    code: str
    level: str
    message: str
    evidence: Any = None


@dataclass(frozen=True)
class AuditResult:
    passed: bool
    findings: tuple[Finding, ...]
    files: tuple[dict[str, Any], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "llm-eval-capsule.audit/v1",
            "passed": self.passed,
            "findings": [asdict(item) for item in self.findings],
            "files": list(self.files),
        }


DEFAULT_POLICY: dict[str, Any] = {
    "require_hypothesis": True,
    "require_model_revision": True,
    "forbid_mutable_model_revision": True,
    "require_seed_when_sampling": True,
    "require_dataset_revision": True,
    "forbid_mutable_dataset_revision": True,
    "require_dataset_license": True,
    "require_dataset_split": True,
    "require_contamination_assessment": True,
    "allow_known_contamination": False,
    "allow_pii": False,
    "require_consent_for_pii": True,
    "require_prompt_files": True,
    "require_metric_evidence": True,
    "minimum_sample_count": 1,
    "forbid_same_model_judge": True,
    "require_blinding_for_human_eval": True,
    "require_inter_rater_for_human_eval": False,
    "require_environment_files": True,
    "forbid_secret_like_paths": True,
    "maximum_file_bytes": 50_000_000,
    "required_artifact_roles": [],
}

MUTABLE_REVISIONS = {"latest", "main", "master", "head", "default", "current"}
SECRET_NAMES = {
    ".env",
    ".env.local",
    "credentials.json",
    "secrets.json",
    "id_rsa",
    "id_ed25519",
    "service-account.json",
}
SECRET_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".jks"}


def _mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CapsuleError(f"{name} must be a JSON object")
    return value


def _list_of_maps(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _digest_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_secret_like(relative: str) -> bool:
    path = Path(relative)
    lowered = path.name.casefold()
    return (
        lowered in SECRET_NAMES
        or path.suffix.casefold() in SECRET_SUFFIXES
        or "secret" in lowered
        or "credential" in lowered
        or lowered.endswith("token.json")
    )


def references(study: dict[str, Any]) -> list[tuple[str, str]]:
    """Return referenced relative paths and their evidence roles."""
    result: list[tuple[str, str]] = []
    for item in _list_of_maps(study.get("datasets")):
        if _nonempty(item.get("path")):
            result.append((item["path"], "dataset"))
    for item in _list_of_maps(study.get("prompts")):
        if _nonempty(item.get("path")):
            result.append((item["path"], "prompt"))
    for item in _list_of_maps(study.get("evaluators")):
        if _nonempty(item.get("rubric_path")):
            result.append((item["rubric_path"], "rubric"))
    for item in _list_of_maps(study.get("metrics")):
        if _nonempty(item.get("evidence_path")):
            result.append((item["evidence_path"], "metric-evidence"))
    environment = study.get("environment_files")
    if isinstance(environment, list):
        result.extend((item, "environment") for item in environment if _nonempty(item))
    for item in _list_of_maps(study.get("artifacts")):
        if _nonempty(item.get("path")):
            role = str(item.get("role") or "artifact")
            result.append((item["path"], role))
    return result


def _inspect_path(root: Path, relative: str, roles: Iterable[str], maximum: int) -> tuple[dict[str, Any] | None, Finding | None]:
    path = Path(relative)
    if path.is_absolute():
        return None, Finding("path.absolute", "error", "artifact paths must be relative", relative)
    base = root.resolve()
    candidate = base / path
    try:
        # Detect lexical traversal before existence checks so a missing
        # ../../target is still classified as an escape, not merely missing.
        candidate.resolve(strict=False).relative_to(base)
    except (OSError, ValueError):
        return None, Finding("path.escape", "error", "artifact resolves outside the study root", relative)
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(base)
    except FileNotFoundError:
        return None, Finding("path.missing", "error", "referenced artifact does not exist", relative)
    except (OSError, ValueError):
        return None, Finding("path.escape", "error", "artifact resolves outside the study root", relative)
    if not resolved.is_file():
        return None, Finding("path.not-file", "error", "referenced artifact is not a regular file", relative)
    size = resolved.stat().st_size
    if size > maximum:
        return None, Finding("path.too-large", "error", "artifact exceeds maximum_file_bytes", {"path": relative, "bytes": size})
    normalized = path.as_posix()
    return {
        "path": normalized,
        "roles": sorted(set(map(str, roles))),
        "bytes": size,
        "sha256": _digest_file(resolved),
    }, None


def audit(study: dict[str, Any], root: str | Path = ".", policy: dict[str, Any] | None = None) -> AuditResult:
    study = _mapping(study, "study")
    rules = dict(DEFAULT_POLICY)
    if policy is not None:
        rules.update(_mapping(policy, "policy"))
    findings: list[Finding] = []

    def add(code: str, message: str, evidence: Any = None, level: str = "error") -> None:
        findings.append(Finding(code, level, message, evidence))

    if study.get("schema") != "llm-eval-capsule.study/v1":
        add("study.schema", "schema must be llm-eval-capsule.study/v1", study.get("schema"))
    for field in ("study_id", "title", "task"):
        if not _nonempty(study.get(field)):
            add(f"study.{field}", f"{field} is required")
    if rules["require_hypothesis"] and not _nonempty(study.get("hypothesis")):
        add("study.hypothesis", "a falsifiable hypothesis is required")

    models = _list_of_maps(study.get("models"))
    if not models:
        add("models.missing", "at least one model is required")
    model_ids: set[str] = set()
    sut_ids: set[str] = set()
    for index, model in enumerate(models):
        model_id = str(model.get("id") or "")
        if not model_id:
            add("model.id", "model id is required", index)
        elif model_id in model_ids:
            add("model.id-duplicate", "model ids must be unique", model_id)
        model_ids.add(model_id)
        if model.get("role") == "system-under-test":
            sut_ids.add(model_id)
        if not _nonempty(model.get("name")) or not _nonempty(model.get("provider")):
            add("model.identity", "model name and provider are required", model_id or index)
        revision = str(model.get("revision") or "")
        if rules["require_model_revision"] and not revision:
            add("model.revision", "an immutable model revision is required", model_id)
        if rules["forbid_mutable_model_revision"] and revision.casefold() in MUTABLE_REVISIONS:
            add("model.revision-mutable", "mutable model revision is not reproducible", {"id": model_id, "revision": revision})
        parameters = model.get("parameters") if isinstance(model.get("parameters"), dict) else {}
        temperature = parameters.get("temperature", 0)
        top_p = parameters.get("top_p", 1)
        sampling = isinstance(temperature, (int, float)) and temperature > 0
        sampling = sampling or (isinstance(top_p, (int, float)) and top_p < 1)
        if rules["require_seed_when_sampling"] and sampling and parameters.get("seed") is None:
            add("model.seed", "sampling parameters require an explicit seed", model_id)
    if models and not sut_ids:
        add("model.sut", "one model must have role system-under-test")

    datasets = _list_of_maps(study.get("datasets"))
    if not datasets:
        add("datasets.missing", "at least one evaluation dataset is required")
    for index, dataset in enumerate(datasets):
        name = dataset.get("name") or index
        for required, code in (
            ("revision", "dataset.revision"),
            ("license", "dataset.license"),
            ("split", "dataset.split"),
        ):
            if rules[f"require_dataset_{required}"] and not _nonempty(dataset.get(required)):
                add(code, f"dataset {required} is required", name)
        revision = str(dataset.get("revision") or "")
        if rules["forbid_mutable_dataset_revision"] and revision.casefold() in MUTABLE_REVISIONS:
            add("dataset.revision-mutable", "mutable dataset revision is not reproducible", {"name": name, "revision": revision})
        assessment = dataset.get("contamination_assessment")
        if rules["require_contamination_assessment"] and assessment in (None, "", "not_checked"):
            add("dataset.contamination-unchecked", "dataset contamination must be assessed", name)
        if not rules["allow_known_contamination"] and assessment == "known_overlap":
            add("dataset.contamination-known", "known train/eval overlap is forbidden", name)
        if dataset.get("contains_pii") is True and not rules["allow_pii"]:
            add("dataset.pii", "PII-containing evaluation data is forbidden", name)
        if dataset.get("contains_pii") is True and rules["require_consent_for_pii"] and dataset.get("consent_documented") is not True:
            add("dataset.consent", "PII data requires documented consent or another lawful basis", name)

    prompts = _list_of_maps(study.get("prompts"))
    if rules["require_prompt_files"] and (not prompts or any(not _nonempty(item.get("path")) for item in prompts)):
        add("prompts.missing", "versioned prompt files are required")

    evaluators = _list_of_maps(study.get("evaluators"))
    if not evaluators:
        add("evaluators.missing", "at least one evaluator is required")
    for evaluator in evaluators:
        name = evaluator.get("name") or "unnamed"
        kind = evaluator.get("kind")
        if kind not in {"rule", "model", "human"}:
            add("evaluator.kind", "evaluator kind must be rule, model, or human", name)
        if not _nonempty(evaluator.get("rubric_path")):
            add("evaluator.rubric", "every evaluator requires a versioned rubric file", name)
        if kind == "model":
            model_ref = evaluator.get("model_ref")
            if model_ref not in model_ids:
                add("evaluator.model-ref", "model evaluator must reference a declared model", model_ref)
            if rules["forbid_same_model_judge"] and model_ref in sut_ids:
                add("evaluator.self-judge", "system-under-test cannot judge itself", model_ref)
        if kind == "human" and rules["require_blinding_for_human_eval"] and evaluator.get("blinded") is not True:
            add("evaluator.blinding", "human evaluation must be blinded", name)
        if kind == "human" and rules["require_inter_rater_for_human_eval"] and not evaluator.get("inter_rater_method"):
            add("evaluator.inter-rater", "human evaluation requires an inter-rater method", name)

    metrics = _list_of_maps(study.get("metrics"))
    if not metrics:
        add("metrics.missing", "at least one metric is required")
    for metric in metrics:
        name = metric.get("name") or "unnamed"
        if metric.get("value") is None:
            add("metric.value", "metric value is required", name)
        if rules["require_metric_evidence"] and not _nonempty(metric.get("evidence_path")):
            add("metric.evidence", "metric evidence file is required", name)
        sample_count = metric.get("sample_count")
        if not isinstance(sample_count, int) or sample_count < int(rules["minimum_sample_count"]):
            add("metric.sample-count", "metric sample_count is below policy", {"metric": name, "sample_count": sample_count})

    environment = study.get("environment_files")
    if rules["require_environment_files"] and (not isinstance(environment, list) or not environment):
        add("environment.missing", "at least one environment or dependency file is required")

    refs: dict[str, set[str]] = {}
    for relative, role in references(study):
        refs.setdefault(relative, set()).add(role)
    required_roles = set(map(str, rules.get("required_artifact_roles", [])))
    present_roles = {role for roles in refs.values() for role in roles}
    if missing := sorted(required_roles - present_roles):
        add("artifacts.required-roles", "required artifact roles are missing", missing)

    records: list[dict[str, Any]] = []
    maximum = int(rules["maximum_file_bytes"])
    for relative, roles in sorted(refs.items()):
        if rules["forbid_secret_like_paths"] and _is_secret_like(relative):
            add("path.secret-like", "secret-like artifact path is forbidden", relative)
            continue
        record, issue = _inspect_path(Path(root), relative, roles, maximum)
        if issue:
            findings.append(issue)
        elif record:
            records.append(record)
    if not findings:
        findings.append(Finding("audit.pass", "info", "all reproducibility checks passed"))
    passed = not any(item.level == "error" for item in findings)
    return AuditResult(passed, tuple(findings), tuple(records))


def effective_policy(policy: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return the complete policy that governs an audit."""
    rules = dict(DEFAULT_POLICY)
    if policy is not None:
        rules.update(_mapping(policy, "policy"))
    return rules


def capture(study: dict[str, Any], root: str | Path = ".", policy: dict[str, Any] | None = None) -> dict[str, Any]:
    rules = effective_policy(policy)
    result = audit(study, root, rules)
    payload = {
        "study": study,
        "policy": rules,
        "passed": result.passed,
        "findings": [asdict(item) for item in result.findings],
        "files": list(result.files),
    }
    capsule_id = hashlib.sha256(_canonical(payload)).hexdigest()
    return {
        "schema": "llm-eval-capsule.capsule/v1",
        "capsule_id": f"sha256:{capsule_id}",
        "captured_at": datetime.now(timezone.utc).isoformat(),
        **payload,
    }


def verify(capsule: dict[str, Any], root: str | Path = ".") -> list[Finding]:
    capsule = _mapping(capsule, "capsule")
    findings: list[Finding] = []
    if capsule.get("schema") != "llm-eval-capsule.capsule/v1":
        findings.append(Finding("capsule.schema", "error", "unsupported capsule schema", capsule.get("schema")))
        return findings
    payload = {
        "study": capsule.get("study"),
        "policy": capsule.get("policy"),
        "passed": capsule.get("passed"),
        "findings": capsule.get("findings"),
        "files": capsule.get("files"),
    }
    expected_id = "sha256:" + hashlib.sha256(_canonical(payload)).hexdigest()
    if capsule.get("capsule_id") != expected_id:
        findings.append(Finding("capsule.id-mismatch", "error", "capsule metadata was modified", {"expected": expected_id, "actual": capsule.get("capsule_id")}))
    for item in _list_of_maps(capsule.get("files")):
        relative = item.get("path")
        if not _nonempty(relative):
            findings.append(Finding("verify.path", "error", "capsule contains an invalid file path"))
            continue
        record, issue = _inspect_path(Path(root), relative, item.get("roles", []), max(int(item.get("bytes", 0)), 1))
        if issue:
            findings.append(issue)
            continue
        if record and record["bytes"] != item.get("bytes"):
            findings.append(Finding("verify.size", "error", "artifact size changed", relative))
        if record and record["sha256"] != item.get("sha256"):
            findings.append(Finding("verify.sha256", "error", "artifact digest changed", relative))
    if capsule.get("passed") is not True:
        findings.append(Finding("capsule.not-approved", "warning", "capsule was captured with policy failures"))
    if not findings:
        findings.append(Finding("verify.pass", "info", "capsule metadata and every artifact digest match"))
    return findings


def _file_digest(capsule: dict[str, Any], path: Any) -> str | None:
    for item in _list_of_maps(capsule.get("files")):
        if item.get("path") == path:
            return item.get("sha256")
    return None


def _semantic(capsule: dict[str, Any]) -> dict[str, Any]:
    study = _mapping(capsule.get("study"), "capsule.study")
    models = _list_of_maps(study.get("models"))
    datasets = []
    for item in _list_of_maps(study.get("datasets")):
        datasets.append({**item, "file_sha256": _file_digest(capsule, item.get("path"))})
    prompts = []
    for item in _list_of_maps(study.get("prompts")):
        prompts.append({**item, "file_sha256": _file_digest(capsule, item.get("path"))})
    evaluators = []
    for item in _list_of_maps(study.get("evaluators")):
        evaluators.append({**item, "rubric_sha256": _file_digest(capsule, item.get("rubric_path"))})
    environment = [{"path": path, "sha256": _file_digest(capsule, path)} for path in study.get("environment_files", [])]
    artifacts = []
    for item in _list_of_maps(study.get("artifacts")):
        artifacts.append({**item, "sha256": _file_digest(capsule, item.get("path"))})
    return {
        "design": {key: study.get(key) for key in ("task", "hypothesis", "declarations")},
        "policy": capsule.get("policy"),
        "sut_models": [item for item in models if item.get("role") == "system-under-test"],
        "support_models": [item for item in models if item.get("role") != "system-under-test"],
        "datasets": datasets,
        "prompts": prompts,
        "evaluators": evaluators,
        "environment": environment,
        "metrics": study.get("metrics", []),
        "artifacts": artifacts,
    }


def compare(baseline: dict[str, Any], current: dict[str, Any], mode: str = "exact") -> list[Finding]:
    if mode not in {"exact", "model-comparison"}:
        raise CapsuleError("mode must be exact or model-comparison")
    before, after = _semantic(baseline), _semantic(current)
    findings: list[Finding] = []

    def check(field: str, level: str, message: str) -> None:
        if before[field] != after[field]:
            findings.append(Finding(f"drift.{field}", level, message, {"before": before[field], "after": after[field]}))

    check("design", "error", "study design or declarations changed")
    check("policy", "error", "reproducibility policy changed")
    check("sut_models", "info" if mode == "model-comparison" else "error", "system-under-test configuration changed")
    check("support_models", "error", "support or judge model configuration changed")
    check("datasets", "error", "evaluation dataset identity or digest changed")
    check("prompts", "error", "prompt identity or digest changed")
    check("evaluators", "error", "evaluator or rubric changed")
    check("environment", "warning", "environment files changed")
    check("metrics", "info", "reported metric values or evidence references changed")
    check("artifacts", "warning", "additional artifact inventory changed")
    if not findings:
        findings.append(Finding("drift.none", "info", "no semantic study drift detected"))
    return findings


def render_report(capsule: dict[str, Any], language: str = "en") -> str:
    if language not in {"en", "zh"}:
        raise CapsuleError("language must be en or zh")
    study = _mapping(capsule.get("study"), "capsule.study")
    zh = language == "zh"
    labels = {
        "title": "LLM 评测复现记录" if zh else "LLM Evaluation Reproducibility Record",
        "identity": "实验信息" if zh else "Study identity",
        "models": "模型" if zh else "Models",
        "datasets": "评测数据集" if zh else "Evaluation datasets",
        "metrics": "指标" if zh else "Metrics",
        "integrity": "完整性" if zh else "Integrity",
        "passed": "通过" if zh else "Passed",
        "failed": "未通过" if zh else "Failed",
    }
    lines = [f"# {labels['title']}: {study.get('title', '')}", "", f"- Capsule ID: `{capsule.get('capsule_id', '')}`", f"- {labels['integrity']}: **{labels['passed'] if capsule.get('passed') else labels['failed']}**", f"- Captured: `{capsule.get('captured_at', '')}`", "", f"## {labels['identity']}", "", f"- Study ID: `{study.get('study_id', '')}`", f"- Task: {study.get('task', '')}", f"- Hypothesis: {study.get('hypothesis', '')}", "", f"## {labels['models']}", ""]
    for item in _list_of_maps(study.get("models")):
        lines.append(f"- `{item.get('id', '')}`: {item.get('provider', '')}/{item.get('name', '')} @ `{item.get('revision', '')}` ({item.get('role', '')})")
    lines.extend(["", f"## {labels['datasets']}", ""])
    for item in _list_of_maps(study.get("datasets")):
        lines.append(f"- {item.get('name', '')} / {item.get('split', '')} @ `{item.get('revision', '')}`; license: `{item.get('license', '')}`")
    lines.extend(["", f"## {labels['metrics']}", "", "| Metric | Value | Samples | Evidence |", "|---|---:|---:|---|"])
    for item in _list_of_maps(study.get("metrics")):
        lines.append(f"| {item.get('name', '')} | {item.get('value', '')} | {item.get('sample_count', '')} | `{item.get('evidence_path', '')}` |")
    lines.extend(["", "---", "", "Generated by LLM Eval Capsule. SHA-256 digests detect changes but are not digital signatures.", ""])
    return "\n".join(lines)
