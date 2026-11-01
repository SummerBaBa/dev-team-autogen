# AutoGen Multi-Agent Dev Team — 多Agent协作安全开发流水线

基于 Microsoft **AutoGen 0.7** 的多Agent 协作系统，模拟软件开发团队（PM → Developer → QA），配备完整的安全护栏与评估追踪机制。

## 架构概览

```
需求输入
  │
  ├── [Input Guard] ─── 提示注入检测、敏感话题过滤
  │
  ▼
┌──────────┐    ┌──────────┐    ┌──────────┐
│   PM     │ →  │   Dev    │ →  │   QA     │
│ 需求分析 │    │ 编码实现 │    │ 测试验证 │
└──────────┘    └──────────┘    └──────────┘
                  │                              ┌──────────┐
                  └── 修复 ← [Fix] ← 测试报告 ← │   QA     │
                                                │ 回归验证 │
                                                └──────────┘
  │
  ├── [Output Guard] ─── 有害代码检测、密钥泄露防护
  ├── [Flow Guard] ───── 审计日志、异常熔断
  └── [Metrics/Trace] ── 性能追踪、评估报告
```

## 快速开始

### 环境要求

- Python 3.10+
- 本地 LLM 服务（兼容 OpenAI API，如 [oMLX](https://github.com/microsoft/omlx)）

### 安装

```bash
pip install -r requirements.txt
```

### 配置

通过环境变量或 `config.py` 配置模型：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `API_BASE` | `http://127.0.0.1:9000/v1` | LLM API 地址 |
| `API_KEY` | `12345678` | API 密钥 |
| `PM_MODEL` | `Qwen3-8B-MLX-4bit` | PM 和 QA 所用模型 |
| `DEV_MODEL` | `Qwen3.8-27B-MLX-4bit` | Developer 所用模型 |

### 运行

```bash
python main.py "开发 Python 命令行 Todo List，支持添加/查看/完成/删除任务，JSON 持久化"
```

### 运行 Benchmark

```bash
python -c "import asyncio; from evaluation.benchmark import BenchmarkRunner; from main import pipeline; asyncio.run(BenchmarkRunner(pipeline, max_rounds=3).run_all())"
```

## 输出产物

| 文件 | 说明 |
|------|------|
| `output/01_PRD.md` | 产品需求文档 |
| `output/02_CODE.md` | 初始代码 |
| `output/03_TEST_REPORT.md` | 测试报告 |
| `output/04_FIXED_CODE.md` | 修复后代码 |
| `output/05_REGRESSION_REPORT.md` | 回归验证报告 |
| `output/audit_log.json` | 审计日志 |
| `output/traces/` | 追踪数据 |
| `output/benchmark_result.json` | Benchmark 评估结果 |

## 安全护栏

详见 [docs/guardrails.md](docs/guardrails.md)

- **Input Guard**: 提示注入检测、敏感话题过滤、输入长度限制
- **Output Guard**: 有害代码检测、密钥泄露防护、输出长度限制
- **Flow Guard**: 全流程审计日志、连续失败熔断

## 评估追踪

详见 [docs/evaluation.md](docs/evaluation.md)

- **Metrics**: 完成率、耗时、token 消耗、护栏阻断统计
- **Tracing**: 结构化调用链追踪
- **Benchmark**: 多需求自动化评估

## 项目结构

```
dev-team-autogen/
├── main.py              # 主流水线入口
├── config.py            # 全局配置
├── agents/              # Agent 定义与注册
│   ├── pm.py           # PM Agent
│   ├── dev.py          # Developer / Fixer Agent
│   ├── qa.py           # QA Agent
│   ├── factory.py      # Agent 工厂
│   └── registry.py     # Agent 注册表
├── guardrails/          # 安全护栏
│   ├── input_guard.py  # 输入护栏
│   ├── output_guard.py # 输出护栏
│   └── flow_guard.py   # 流程护栏
├── evaluation/          # 评估追踪
│   ├── metrics.py      # 指标追踪
│   ├── tracing.py      # 调用链追踪
│   └── benchmark.py    # Benchmark 运行器
├── tests/               # 单元测试
├── output/              # 运行产物
└── docs/                # 文档
    ├── architecture.md # 架构设计
    ├── guardrails.md   # 安全护栏
    └── evaluation.md   # 评估系统
```

## 许可

MIT
