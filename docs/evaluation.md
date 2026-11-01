# 评估与追踪系统

## 概述

评估系统提供三种互补能力：

1. **指标追踪（Metrics）**：每次运行的定量测量
2. **调用链追踪（Tracing）**：结构化的日志记录，用于调试和分析
3. **基准测试（Benchmarking）**：多需求自动化评估

## 指标追踪

**源码位置**: `evaluation/metrics.py`

### 单次运行指标

| 指标 | 说明 | 计算方式 |
|------|------|---------|
| **完成率** | 完成的阶段比例 | `completed_phases / total_phases` |
| **总耗时** | 端到端流水线时间 | 所有阶段耗时之和 |
| **平均阶段耗时** | 每次 Agent 调用平均耗时 | `total_duration / num_phases` |
| **护栏阻断次数** | 安全护栏拦截次数 | 阻断事件计数 |
| **Token 估算** | 近似 Token 用量 | 输入+输出字符数之和 |

### 阶段级指标

每次 Agent 调用记录：

- `phase`: 流水线阶段名称
- `agent`: Agent 名称
- `duration_ms`: 墙钟时间
- `input_chars` / `output_chars`: 文本量
- `status`: completed / failed / blocked
- `guardrail_result`: none / passed / blocked

## 调用链追踪

**源码位置**: `evaluation/tracing.py`

### 追踪事件

每次运行的完整事件链：

```
pipeline_start
  ├── guardrail_check (input)
  ├── llm_call (PM)
  │   ├── guardrail_check (output)
  │   └── llm_response
  ├── llm_call (Developer)
  │   ├── guardrail_check (output)
  │   └── llm_response
  ├── llm_call (QA)
  │   └── ...
  ├── llm_call (Fixer)
  │   └── ...
  └── pipeline_complete
```

### 追踪输出

追踪文件以 JSON 格式保存在 `output/traces/`：

```
output/traces/
├── trace_20260528_143022.json
└── trace_20260528_150145.json
```

每个追踪文件包含：
- `session_id`: 唯一会话标识
- `events[]`: 按时间排序的事件列表

### 应用场景

- **调试**: 定位导致流水线失败的 Agent
- **性能分析**: 找出慢速的 Agent 调用
- **安全审计**: 审查所有护栏事件
- **成本分析**: 估算每次运行的 Token 用量

## 基准测试

**源码位置**: `evaluation/benchmark.py`

### 测试需求集

内置五个代表性需求：

1. **Todo List CLI** — 任务管理，JSON 持久化
2. **计算器 CLI** — 加减乘除，历史记录，平方根
3. **笔记管理 CLI** — 增删改查笔记，文件持久化
4. **密码生成器 CLI** — 可配置的密码生成
5. **文件整理 CLI** — 按扩展名自动分类文件

### 运行 Benchmark

```bash
python -c "
import asyncio
from evaluation.benchmark import BenchmarkRunner
from main import pipeline
asyncio.run(BenchmarkRunner(pipeline, max_rounds=3).run_all())
"
```

### Benchmark 输出

```json
{
  "benchmark_time": "2026-05-28T14:30:00",
  "summary": {
    "num_runs": 3,
    "avg_completion_rate": 0.87,
    "avg_duration_ms": 285000,
    "total_guardrail_blocks": 2
  },
  "runs": [
    {
      "requirement": "开发 Python 命令行 Todo List...",
      "completion_rate": 1.0,
      "total_duration_ms": 240000,
      "guardrail_blocks": 0,
      "phases": 5
    }
  ]
}
```

## 外部可观测性集成

追踪层设计为可扩展的。对接 Langfuse 或 Arize Phoenix 等外部平台：

1. 实现新的追踪处理器，继承 `TraceLogger` 接口
2. 通过 HTTP API 将事件转发到外部服务
3. 通过 `EvalConfig` 配置切换

示例集成代码：

```python
class LangfuseHandler(TraceLogger):
    def log(self, phase, agent, event_type, content, duration_ms, metadata=None):
        super().log(phase, agent, event_type, content, duration_ms, metadata)
        # 转发到 Langfuse API
        langfuse.trace(...)
```


## LangSmith 集成（推荐）

**源码位置**: `main.py`（`@traceable` 装饰器）, `evaluation/evaluators.py`, `evaluation/benchmark.py`

LangSmith 提供端到端的 Tracing + Evaluation，替换现有的自建追踪与基准测试方案。

### 架构

```
main.py: pipeline()
  └── @traceable(run_type="chain")       ← 自动捕获整个流水线
       ├── run_agent(PM)
       │   └── @traceable(run_type="chain")  ← 自动捕获每个 Agent 调用
       ├── run_agent(Developer)
       ├── run_agent(QA)
       ├── run_agent(Fixer)
       └── run_agent(QA Regression)
            ↓
       Returns metrics dict with artifacts
            ↓
evaluation/evaluators.py
  ├── completion_evaluator       → 完成率打分
  ├── duration_evaluator         → 耗时打分
  ├── code_validity_evaluator    → AST 语法检查
  ├── guardrail_evaluator        → 护栏拦截率
  └── _make_llm_judge_evaluator  → LLM-as-Judge 综合质量评分
       ↓
evaluation/benchmark.py: run_langsmith_evaluation()
  └── langsmith.evaluate(target_fn, data=Dataset, evaluators=[...])
       ↓
LangSmith Dashboard  ← 可视化 + 版本对比 + 人工评审
```

### 相对于自建方案的优势

| 维度 | 自建 (TraceLogger + BenchmarkRunner) | LangSmith |
|------|--------------------------------------|-----------|
| 存储 | 本地 JSON 文件 | 云端/自托管数据库 |
| 可视化 | 无 | 内置 Web Dashboard |
| 版本对比 | 手动 diff | 一键对比不同实验 |
| 评估器 | 需自写 | 内置 + 可扩展 |
| LLM-as-Judge | 不适用 | 原生支持 |
| 人工评审 | 不适用 | 批注/标注/反馈 |
| CI/CD 集成 | 需自建 | 原生 API + GitHub Actions |
| 团队协作 | 单机 | 多用户共享 |

### 配置

```bash
# 基础 Tracing（仅需环境变量）
export LANGSMITH_TRACING=true
export LANGSMITH_API_KEY='xxxxxx'   # https://smith.langchain.com 获取
export LANGSMITH_PROJECT=dev-team-autogen  # 可选，默认 langchain

# 运行一次即可在 Dashboard 看到 Trace
python main.py "开发 Python 计算器 CLI"
```

### 运行 Evaluation

```bash
# 需要先配置 Tracing 环境变量
python -c "
from evaluation.benchmark import run_langsmith_evaluation
run_langsmith_evaluation(max_requirements=3)
"
```

执行流程：
1. 自动创建/复用 LangSmith Dataset（基于 `BENCHMARK_REQUIREMENTS`）
2. 对每条需求运行完整流水线
3. 应用 4 个内置 Evaluator（完成率、耗时、代码语法、护栏健康）
4. 结果自动上传到 LangSmith Dashboard

### Evaluator 说明

| Evaluator | 类型 | 评分逻辑 |
|-----------|------|---------|
| `completion_evaluator` | 启发式 | 完成阶段数 / 总阶段数 |
| `duration_evaluator` | 启发式 | max(0, 1 - 耗时/10min) |
| `code_validity_evaluator` | 规则 | 对代码做 ast.parse() 检查 |
| `guardrail_evaluator` | 启发式 | 1 - (拦截数 / 阶段数) |
| `_make_llm_judge_evaluator` | LLM-as-Judge | LLM 对 5 个维度打分 |

### 迁移路线

| Phase | 内容 | 状态 |
|-------|------|------|
| 1 — Tracing | `@traceable` 替换 `TraceLogger` | ✅ 已完成 |
| 2 — Evaluation | `evaluate()` 替换 `BenchmarkRunner.run_all()` | ✅ 已完成 |
| 3 — Regression | CI 门禁 + 回归测试 | 🔜 后续 |

### 在 CI 中使用

```yaml
# .github/workflows/eval.yml
- name: Run LangSmith Evaluation
  env:
    LANGSMITH_API_KEY: ${{ secrets.LANGSMITH_API_KEY }}
  run: |
    pip install langsmith
    python -c "
    from evaluation.benchmark import run_langsmith_evaluation
    results = run_langsmith_evaluation(max_requirements=3)
    assert results.average_completion > 0.8, 'Completion rate too low'
    "
```
