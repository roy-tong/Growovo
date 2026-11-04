"""Value Artifact：六类资产、复现契约、业务渲染与价值事件边界。"""
import copy
import unittest

from core import value_artifact


def _artifact(kind="tutorial"):
    return {
        "id": "artifact-1",
        "kind": kind,
        "title": "从访谈到可回溯结论",
        "scenario": {
            "user": "产品经理",
            "trigger": "访谈记录散落在多个文档",
            "task": "把同一需求的原话、来源和判断连成可复核结论",
            "current_substitute": "手工复制到表格",
        },
        "evidence": [{
            "summary": "3 次可用性观察中，参与者都需要回查原始访谈",
            "source_ref": "research/usability-2026-10#observations",
        }],
        "direct_user_value": "不用重读全部访谈即可复核一条结论的依据",
        "reproduction_steps": [
            {"action": "导入同一份匿名访谈 fixture", "expected_result": "生成带稳定来源 ID 的记录"},
            {"action": "打开一条结论并回查原话", "expected_result": "原话与来源 ID 一致"},
        ],
        "limitations": ["只覆盖中文访谈 fixture，语义判断仍需人工复核"],
        "platform": "github",
        "motion": "reproducible-tutorial",
        "vertical": "product-research",
        "observation_events": [{
            "name": "读者独立复核结论",
            "event_type": "task_completed",
            "source": "product-events",
            "approximated": False,
        }],
    }


class ValueArtifactTests(unittest.TestCase):
    def test_empty_artifact_reports_all_required_areas(self):
        errors = value_artifact.lint({})
        for field in ("kind", "scenario", "direct_user_value", "reproduction_steps",
                      "limitations", "platform", "observation_events"):
            self.assertTrue(any(field in error for error in errors), field)
        with self.assertRaises(value_artifact.ArtifactError):
            value_artifact.build({})

    def test_valid_tutorial_builds_without_mutating_and_renders_business_body(self):
        source = _artifact()
        snapshot = copy.deepcopy(source)
        artifact = value_artifact.build(source)
        self.assertEqual(snapshot, source)
        self.assertEqual("1.0", artifact["schema_version"])
        self.assertTrue(artifact["value_policy"]["attention_is_not_value"])
        rendered = value_artifact.render(source)
        for text in ("从访谈到可回溯结论", "需求场景", "不用重读全部访谈",
                     "research/usability-2026-10", "复现步骤", "预期结果",
                     "github × reproducible-tutorial × product-research",
                     "语义判断仍需人工复核", "观察什么才算用户价值"):
            self.assertIn(text, rendered)
        self.assertIn("曝光、阅读、点击、发送和报名", rendered)

    def test_all_six_artifact_types_have_specific_contracts(self):
        cases = []
        cases.append(_artifact("tutorial"))
        example = _artifact("example")
        example["body"] = "完整输入、操作和输出样例"
        cases.append(example)
        cases.append(_artifact("reproducible_test"))
        template = _artifact("template")
        template["template_body"] = "需求｜原话｜来源｜反证｜判断"
        cases.append(template)
        comparison = _artifact("comparison")
        comparison["comparison"] = {
            "criteria": ["来源可回溯", "复现成本"],
            "alternatives": [{"name": "手工表格"}, {"name": "当前工作流"}],
        }
        cases.append(comparison)
        real = _artifact("real_case")
        real["observation_events"][0].update({
            "observed": True, "subject": "case-anonymous-01",
            "ts": "2026-11-03T10:00:00+08:00", "source_ref": "product-events/case-01",
        })
        cases.append(real)
        for artifact in cases:
            with self.subTest(kind=artifact["kind"]):
                self.assertEqual([], value_artifact.validate(artifact))
                self.assertEqual(artifact["kind"], value_artifact.build(artifact)["kind"])

    def test_example_template_comparison_and_real_case_require_substance(self):
        example = _artifact("example")
        self.assertTrue(any("正文" in error for error in value_artifact.lint(example)))
        template = _artifact("template")
        self.assertTrue(any("正文" in error for error in value_artifact.lint(template)))
        comparison = _artifact("comparison")
        comparison["comparison"] = {"criteria": [], "alternatives": ["只有一个"]}
        errors = value_artifact.lint(comparison)
        self.assertTrue(any("criteria" in error for error in errors))
        self.assertTrue(any("至少要有两个" in error for error in errors))
        real = _artifact("real_case")
        self.assertTrue(any("真实案例" in error for error in value_artifact.lint(real)))

    def test_exposure_cannot_be_value_event_or_direct_value(self):
        artifact = _artifact()
        artifact["observation_events"][0]["event_type"] = "impression"
        errors = value_artifact.lint(artifact)
        self.assertTrue(any("不能作为用户价值" in error for error in errors))
        artifact = _artifact()
        artifact["direct_user_value"] = "提升品牌曝光"
        self.assertTrue(any("不能只是曝光" in error for error in value_artifact.lint(artifact)))

    def test_approximation_and_unverified_observed_value_are_rejected(self):
        artifact = _artifact()
        artifact["observation_events"][0]["approximated"] = True
        self.assertTrue(any("不允许近似" in error for error in value_artifact.lint(artifact)))
        artifact = _artifact()
        artifact["observation_events"][0].update({
            "event_type": "activation", "observed": True, "subject": "u1",
            "ts": "2026-11-03", "source_ref": "product/activation/u1",
        })
        errors = value_artifact.lint(artifact)
        self.assertTrue(any("verification" in error for error in errors))
        artifact["observation_events"][0]["verification"] = {
            "status": "verified", "method": "product-db", "ref": "activation-u1",
        }
        self.assertEqual([], value_artifact.lint(artifact))

    def test_nested_context_and_chinese_kind_are_normalized(self):
        artifact = _artifact("教程")
        artifact["context"] = {
            "platform": artifact.pop("platform"),
            "motion": artifact.pop("motion"),
            "vertical": artifact.pop("vertical"),
        }
        built = value_artifact.build(artifact)
        self.assertEqual("tutorial", built["kind"])
        self.assertEqual("github", built["platform"])
        self.assertEqual("reproducible-tutorial", built["motion"])

    def test_example_body_is_rendered_as_business_content(self):
        artifact = _artifact("example")
        artifact["body"] = "输入三条匿名原话，输出每条结论的来源与反证。"
        rendered = value_artifact.render(artifact)
        self.assertIn("## 业务正文", rendered)
        self.assertIn(artifact["body"], rendered)


if __name__ == "__main__":
    unittest.main()
