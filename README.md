# LLM Eval Capsule：大模型评测复现胶囊

[English](README_EN.md) | 简体中文

[![CI](https://github.com/MikeJack302/llm-eval-capsule/actions/workflows/ci.yml/badge.svg)](https://github.com/MikeJack302/llm-eval-capsule/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-3776AB.svg)](https://www.python.org/)

一个给硕博生和 AI 实验室用的零运行时依赖 CLI：在你报告“某模型提高了 3%”之前，先检查模型 revision、采样参数、评测数据、prompt、judge、rubric、环境和指标证据是否足够复现，再把它们封装成带 SHA-256 完整性证明的实验胶囊。

它不运行模型，也不绑定某个评测框架；你可以把 LightEval、lm-evaluation-harness、自建脚本或 API 实验的输出作为证据文件。

## 它解决什么问题

- 云端模型名称没变，底层版本已更新。
- 设置了 `temperature > 0`，却没有记录 seed。
- 测试集只写了名字，没有 split、revision、许可证或污染检查。
- 被评模型同时充当 judge，分数存在自我偏好风险。
- 论文表格有指标，没有逐样本输出或评分证据。
- prompt、rubric 或依赖环境后来被改了，但实验记录看不出来。
- 分享实验包时，意外把 `.env`、私钥或 credentials 当作 artifact 打包。

## 五个命令

```text
audit    检查实验规范和引用文件
capture  固化完整策略、审计结论与所有文件哈希
verify   验证胶囊元数据和文件是否被修改
diff     区分模型对比与实验条件漂移
report   输出可放进论文附录的中/英文 Markdown 记录
```

## 快速开始

要求 Python 3.10+，安装后的运行时只使用标准库。

```powershell
git clone https://github.com/MikeJack302/llm-eval-capsule.git
cd llm-eval-capsule
python -m pip install .

# 审计示例实验
llm-eval-capsule audit examples/mini-study/study.json `
  --root examples/mini-study `
  --policy examples/mini-study/policy.json

# 固化为不可静默修改的胶囊
llm-eval-capsule capture examples/mini-study/study.json `
  --root examples/mini-study `
  --policy examples/mini-study/policy.json `
  -o capsule.json

# 文件搬到另一台电脑后重新验证
llm-eval-capsule verify capsule.json --root examples/mini-study

# 生成中文论文附录记录
llm-eval-capsule report capsule.json --language zh -o report.md
```

WSL2 / Linux：

```bash
llm-eval-capsule audit examples/mini-study/study.json \
  --root examples/mini-study \
  --policy examples/mini-study/policy.json
```

也可以不安装：

```powershell
$env:PYTHONPATH = "src"
python -m llm_eval_capsule --help
```

## 实验规范

完整示例在 [`examples/mini-study/study.json`](examples/mini-study/study.json)。核心结构如下：

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

所有 `path` 都必须相对于 `--root`。同一个文件可以承担多个角色，例如逐样本输出既是 `metric-evidence`，也是 `raw-output`；胶囊只计算一次哈希并合并角色。

## 策略检查

内置策略默认检查：

| 类别 | 检查内容 |
|---|---|
| 研究设计 | study ID、任务、可证伪假设 |
| 模型 | provider、name、不可变 revision、参数、采样 seed、唯一 ID |
| 数据 | 不可变 revision、split、license、污染评估、PII 与同意说明 |
| Prompt | 必须引用被版本控制的文件 |
| 评测器 | rule/model/human 类型、rubric、judge 引用、自评风险 |
| 人评 | 盲评；可选要求 inter-rater 方法 |
| 指标 | value、sample count、逐样本证据文件 |
| 环境 | lockfile 或环境描述文件 |
| 文件安全 | 缺失、绝对路径、目录穿越、体积上限、secret-like 文件名 |

策略可由 JSON 覆盖，参考 [`examples/mini-study/policy.json`](examples/mini-study/policy.json)。`audit` 和 `capture` 返回稳定退出码，适合 CI。

## 完整性模型

`capture` 计算规范化 JSON 的 SHA-256，绑定：

- 完整实验规范；
- 展开后的完整策略，而不只是用户覆盖项；
- 审计通过状态与每条 finding；
- 每个引用文件的路径、角色、字节数和 SHA-256。

`captured_at` 不参与胶囊 ID，因此同一实验内容在不同时间捕获仍有相同 ID。修改策略、结论、模型配置或任何证据文件都会被 `verify` 发现。

> SHA-256 是完整性摘要，不是数字签名。它能发现变化，但不能证明是谁创建了胶囊。需要身份保证时，请再用 Git 签名、Sigstore 或机构归档系统签署 `capsule.json`。

## 漂移检测

严格复现要求两个胶囊的条件完全一致：

```powershell
llm-eval-capsule diff baseline.json rerun.json --mode exact
```

公平比较两个候选模型时，允许 system-under-test 变化，但数据集、prompt、judge、rubric、策略与研究设计仍须保持一致：

```powershell
llm-eval-capsule diff model-a.json model-b.json --mode model-comparison
```

| 变化 | `exact` | `model-comparison` |
|---|---:|---:|
| 被评模型 | error | info（预期差异） |
| judge / 支持模型 | error | error |
| 数据、prompt、rubric、策略、设计 | error | error |
| 环境 | warning | warning |
| 指标值 | info | info |

## 退出码

| 代码 | 含义 |
|---:|---|
| `0` | 审计/验证通过，或 diff 没有 error |
| `2` | 策略失败、完整性失败或 breaking drift |
| `3` | JSON 缺失、损坏或参数/结构不受支持 |

`audit`、`verify` 和 `diff` 支持 `--format json`。

## 研究边界

- seed 对某些托管 API 只是 best effort，记录 seed 不等于服务端保证逐 token 确定性。
- 数据污染字段是研究者声明；本工具检查“是否记录”，不会自动证明训练数据不存在重叠。
- 文件摘要证明位级一致，不证明数据合法、无偏或结论有效。
- 模型 judge 仍可能有系统偏差；重要研究应结合盲态人评、多个 judge 或规则指标。
- 不要把含隐私数据的原始材料公开上传。策略默认拒绝 PII 声明和疑似密钥路径。

## 测试

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -v
```

CI 覆盖 Windows、Ubuntu、Python 3.10 与 3.13。

## 设计参考

- [Hugging Face Model Cards](https://huggingface.co/docs/hub/model-cards)：模型用途、限制、实验参数、数据集与评测结果记录。
- [Hugging Face Dataset Cards](https://huggingface.co/docs/hub/datasets-cards)：许可证、数据背景、偏差与负责任使用说明。
- [Hugging Face Evaluate](https://huggingface.co/docs/evaluate/index)：指标、测量与模型比较的评测工作流。
- [NIST GenAI Evaluation Program](https://ai-challenges.nist.gov/genai)：生成式 AI 测试与测量科学项目。
- [MLCommons MLPerf Endpoints](https://mlcommons.org/benchmarks/endpoints/)：开放、公平、可复现的生成式 AI endpoint benchmark 目标。

## License

MIT
