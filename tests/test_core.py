from __future__ import annotations

import copy
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from llm_eval_capsule.cli import main
from llm_eval_capsule.core import CapsuleError, audit, capture, compare, references, render_report, verify


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "mini-study"
STUDY = json.loads((EXAMPLE / "study.json").read_text(encoding="utf-8"))
POLICY = json.loads((EXAMPLE / "policy.json").read_text(encoding="utf-8"))


def codes(result):
    return {item.code for item in result.findings}


class ReferenceTests(unittest.TestCase):
    def test_all_reference_roles(self):
        refs = references(STUDY)
        roles = {role for _, role in refs}
        self.assertEqual(roles, {"dataset", "prompt", "rubric", "metric-evidence", "environment", "raw-output"})

    def test_duplicate_file_can_have_multiple_roles(self):
        result = audit(STUDY, EXAMPLE, POLICY)
        output = next(item for item in result.files if item["path"] == "results/predictions.jsonl")
        self.assertEqual(output["roles"], ["metric-evidence", "raw-output"])


class AuditTests(unittest.TestCase):
    def test_good_study_passes(self):
        result = audit(STUDY, EXAMPLE, POLICY)
        self.assertTrue(result.passed)
        self.assertEqual(len(result.files), 5)

    def mutate(self, callback, policy=None):
        value = copy.deepcopy(STUDY)
        callback(value)
        return audit(value, EXAMPLE, policy or POLICY)

    def test_schema_required(self):
        self.assertIn("study.schema", codes(self.mutate(lambda x: x.update(schema="wrong"))))

    def test_identity_required(self):
        self.assertIn("study.study_id", codes(self.mutate(lambda x: x.update(study_id=""))))

    def test_hypothesis_required(self):
        self.assertIn("study.hypothesis", codes(self.mutate(lambda x: x.update(hypothesis=""))))

    def test_model_revision_required(self):
        self.assertIn("model.revision", codes(self.mutate(lambda x: x["models"][0].pop("revision"))))

    def test_mutable_revision_rejected(self):
        self.assertIn("model.revision-mutable", codes(self.mutate(lambda x: x["models"][0].update(revision="latest"))))

    def test_sampling_requires_seed(self):
        self.assertIn("model.seed", codes(self.mutate(lambda x: x["models"][0]["parameters"].pop("seed"))))

    def test_temperature_zero_does_not_require_seed(self):
        result = self.mutate(lambda x: x["models"][0].update(parameters={"temperature": 0}))
        self.assertNotIn("model.seed", codes(result))

    def test_duplicate_model_id(self):
        self.assertIn("model.id-duplicate", codes(self.mutate(lambda x: x["models"][1].update(id="candidate"))))

    def test_sut_required(self):
        self.assertIn("model.sut", codes(self.mutate(lambda x: [m.update(role="evaluator") for m in x["models"]])))

    def test_dataset_revision(self):
        self.assertIn("dataset.revision", codes(self.mutate(lambda x: x["datasets"][0].pop("revision"))))

    def test_mutable_dataset_revision(self):
        self.assertIn("dataset.revision-mutable", codes(self.mutate(lambda x: x["datasets"][0].update(revision="main"))))

    def test_dataset_license(self):
        self.assertIn("dataset.license", codes(self.mutate(lambda x: x["datasets"][0].pop("license"))))

    def test_dataset_split(self):
        self.assertIn("dataset.split", codes(self.mutate(lambda x: x["datasets"][0].pop("split"))))

    def test_contamination_unchecked(self):
        self.assertIn("dataset.contamination-unchecked", codes(self.mutate(lambda x: x["datasets"][0].update(contamination_assessment="not_checked"))))

    def test_known_overlap(self):
        self.assertIn("dataset.contamination-known", codes(self.mutate(lambda x: x["datasets"][0].update(contamination_assessment="known_overlap"))))

    def test_pii_forbidden(self):
        result = self.mutate(lambda x: x["datasets"][0].update(contains_pii=True, consent_documented=False))
        self.assertIn("dataset.pii", codes(result))
        self.assertIn("dataset.consent", codes(result))

    def test_prompt_required(self):
        self.assertIn("prompts.missing", codes(self.mutate(lambda x: x.update(prompts=[]))))

    def test_self_judge_forbidden(self):
        self.assertIn("evaluator.self-judge", codes(self.mutate(lambda x: x["evaluators"][0].update(model_ref="candidate"))))

    def test_unknown_judge_model(self):
        self.assertIn("evaluator.model-ref", codes(self.mutate(lambda x: x["evaluators"][0].update(model_ref="missing"))))

    def test_human_blinding(self):
        def change(value):
            value["evaluators"][0] = {"name": "panel", "kind": "human", "rubric_path": "rubrics/schema.md", "blinded": False}
        self.assertIn("evaluator.blinding", codes(self.mutate(change)))

    def test_human_inter_rater_policy(self):
        policy = {**POLICY, "require_inter_rater_for_human_eval": True}
        def change(value):
            value["evaluators"][0] = {"name": "panel", "kind": "human", "rubric_path": "rubrics/schema.md", "blinded": True}
        self.assertIn("evaluator.inter-rater", codes(self.mutate(change, policy)))

    def test_metric_evidence(self):
        self.assertIn("metric.evidence", codes(self.mutate(lambda x: x["metrics"][0].pop("evidence_path"))))

    def test_metric_sample_count(self):
        self.assertIn("metric.sample-count", codes(self.mutate(lambda x: x["metrics"][0].update(sample_count=2))))

    def test_environment_required(self):
        self.assertIn("environment.missing", codes(self.mutate(lambda x: x.update(environment_files=[]))))

    def test_missing_file(self):
        self.assertIn("path.missing", codes(self.mutate(lambda x: x["prompts"][0].update(path="prompts/missing.txt"))))

    def test_absolute_path_rejected(self):
        absolute = str((EXAMPLE / "prompts/system.txt").resolve())
        self.assertIn("path.absolute", codes(self.mutate(lambda x: x["prompts"][0].update(path=absolute))))

    def test_parent_escape_rejected(self):
        self.assertIn("path.escape", codes(self.mutate(lambda x: x["prompts"][0].update(path="../../README.md"))))

    def test_secret_like_path_rejected_before_read(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".env").write_text("API_KEY=do-not-read", encoding="utf-8")
            value = copy.deepcopy(STUDY)
            value["artifacts"].append({"role": "debug", "path": ".env"})
            result = audit(value, root, {**POLICY, "require_prompt_files": False, "require_environment_files": False, "require_metric_evidence": False})
            self.assertIn("path.secret-like", codes(result))

    def test_maximum_size(self):
        policy = {**POLICY, "maximum_file_bytes": 1}
        self.assertIn("path.too-large", codes(audit(STUDY, EXAMPLE, policy)))

    def test_required_role(self):
        policy = {**POLICY, "required_artifact_roles": ["transcript"]}
        self.assertIn("artifacts.required-roles", codes(audit(STUDY, EXAMPLE, policy)))


class CapsuleTests(unittest.TestCase):
    def setUp(self):
        self.capsule = capture(STUDY, EXAMPLE, POLICY)

    def test_capture_passes(self):
        self.assertTrue(self.capsule["passed"])
        self.assertTrue(self.capsule["capsule_id"].startswith("sha256:"))

    def test_capsule_id_is_deterministic(self):
        other = capture(STUDY, EXAMPLE, POLICY)
        self.assertEqual(self.capsule["capsule_id"], other["capsule_id"])

    def test_verify_passes(self):
        self.assertEqual(verify(self.capsule, EXAMPLE)[0].code, "verify.pass")

    def test_metadata_tamper(self):
        value = copy.deepcopy(self.capsule)
        value["study"]["title"] = "tampered"
        self.assertIn("capsule.id-mismatch", {item.code for item in verify(value, EXAMPLE)})

    def test_pass_status_tamper(self):
        value = copy.deepcopy(self.capsule)
        value["passed"] = False
        self.assertIn("capsule.id-mismatch", {item.code for item in verify(value, EXAMPLE)})

    def test_policy_tamper(self):
        value = copy.deepcopy(self.capsule)
        value["policy"]["minimum_sample_count"] = 1
        self.assertIn("capsule.id-mismatch", {item.code for item in verify(value, EXAMPLE)})

    def test_file_tamper(self):
        with tempfile.TemporaryDirectory() as directory:
            copied = Path(directory) / "study"
            shutil.copytree(EXAMPLE, copied)
            (copied / "prompts/system.txt").write_text("changed", encoding="utf-8")
            self.assertIn("verify.sha256", {item.code for item in verify(self.capsule, copied)})

    def test_missing_file_verify(self):
        with tempfile.TemporaryDirectory() as directory:
            copied = Path(directory) / "study"
            shutil.copytree(EXAMPLE, copied)
            (copied / "prompts/system.txt").unlink()
            self.assertIn("path.missing", {item.code for item in verify(self.capsule, copied)})

    def test_unapproved_capsule_warns(self):
        value = copy.deepcopy(self.capsule)
        value["passed"] = False
        self.assertIn("capsule.not-approved", {item.code for item in verify(value, EXAMPLE)})


class DiffTests(unittest.TestCase):
    def setUp(self):
        self.before = capture(STUDY, EXAMPLE, POLICY)

    def changed(self, callback, mode="exact"):
        study = copy.deepcopy(STUDY)
        callback(study)
        after = capture(study, EXAMPLE, POLICY)
        return {item.code: item for item in compare(self.before, after, mode)}

    def test_no_drift(self):
        self.assertEqual(compare(self.before, self.before)[0].code, "drift.none")

    def test_model_change_breaks_exact(self):
        result = self.changed(lambda x: x["models"][0].update(revision="new-revision"))
        self.assertEqual(result["drift.sut_models"].level, "error")

    def test_model_change_allowed_for_comparison(self):
        result = self.changed(lambda x: x["models"][0].update(revision="new-revision"), "model-comparison")
        self.assertEqual(result["drift.sut_models"].level, "info")

    def test_judge_change_breaks(self):
        result = self.changed(lambda x: x["models"][1].update(revision="new-judge"), "model-comparison")
        self.assertEqual(result["drift.support_models"].level, "error")

    def test_dataset_change_breaks(self):
        result = self.changed(lambda x: x["datasets"][0].update(split="test-v2"))
        self.assertIn("drift.datasets", result)

    def test_prompt_change_breaks(self):
        result = self.changed(lambda x: x["prompts"][0].update(revision="v4"))
        self.assertIn("drift.prompts", result)

    def test_metric_change_is_info(self):
        result = self.changed(lambda x: x["metrics"][0].update(value=0.8))
        self.assertEqual(result["drift.metrics"].level, "info")

    def test_policy_change_breaks(self):
        after = capture(STUDY, EXAMPLE, {**POLICY, "minimum_sample_count": 1})
        result = {item.code: item for item in compare(self.before, after)}
        self.assertEqual(result["drift.policy"].level, "error")

    def test_bad_mode(self):
        with self.assertRaises(CapsuleError):
            compare(self.before, self.before, "wrong")


class ReportAndCliTests(unittest.TestCase):
    def test_english_report(self):
        report = render_report(capture(STUDY, EXAMPLE, POLICY), "en")
        self.assertIn("LLM Evaluation Reproducibility Record", report)
        self.assertIn("exact_match", report)

    def test_chinese_report(self):
        report = render_report(capture(STUDY, EXAMPLE, POLICY), "zh")
        self.assertIn("LLM 评测复现记录", report)

    def test_bad_language(self):
        with self.assertRaises(CapsuleError):
            render_report(capture(STUDY, EXAMPLE, POLICY), "fr")

    def test_cli_audit(self):
        self.assertEqual(main(["audit", str(EXAMPLE / "study.json"), "--root", str(EXAMPLE), "--policy", str(EXAMPLE / "policy.json")]), 0)

    def test_cli_capture_and_verify(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "capsule.json"
            self.assertEqual(main(["capture", str(EXAMPLE / "study.json"), "--root", str(EXAMPLE), "--policy", str(EXAMPLE / "policy.json"), "-o", str(output)]), 0)
            self.assertEqual(main(["verify", str(output), "--root", str(EXAMPLE)]), 0)

    def test_cli_report(self):
        with tempfile.TemporaryDirectory() as directory:
            capsule_path = Path(directory) / "capsule.json"
            report_path = Path(directory) / "report.md"
            capsule_path.write_text(json.dumps(capture(STUDY, EXAMPLE, POLICY)), encoding="utf-8")
            self.assertEqual(main(["report", str(capsule_path), "--language", "zh", "-o", str(report_path)]), 0)
            self.assertIn("评测复现", report_path.read_text(encoding="utf-8"))

    def test_cli_input_error(self):
        self.assertEqual(main(["audit", "missing.json"]), 3)


if __name__ == "__main__":
    unittest.main()
