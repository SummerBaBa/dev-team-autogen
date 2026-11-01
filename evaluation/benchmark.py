"""
基准测试与评估框架

本模块提供两种评估流水线质量的方法：

  1. 当前方案：LangSmith Evaluation（推荐）
     使用 langsmith.evaluate() 运行标准化评估，结果展示在 LangSmith
     Dashboard，支持版本对比、CI/CD 集成和人工审核。

  2. 遗留方案：BenchmarkRunner（已弃用，代码注释保留作参考）
     自定义内存指标 + 本地 JSON 文件，需要手动对比。

──
迁移理由（LangSmith 的优势）：
  - 集中式 Dashboard： 在 LangSmith UI 中可视化所有运行结果
  - 版本控制：        自动对比模型/提示词变更前后的效果
  - 标准化指标：       内置比较和回归检测机制
  - 人工审核：        支持协作评审和标注
  - CI/CD 就绪：       原生集成测试流水线
  - LLM-as-Judge：     无需人工评审即可进行结构化质量评分
  - 减少样板代码：     20+ 行自定义追踪 → 1 行 evaluate() 调用

迁移策略（三个阶段）：
  Phase 1 - 追踪（已完成）：  TraceLogger → @traceable 装饰器
  Phase 2 - 评估（当前阶段）： 创建 Dataset，定义 evaluators，替换 BenchmarkRunner
  Phase 3 - 回归测试（下一阶段）： 基于评估分数设置 CI 质量门禁

可在简历中描述为：
  "设计并实现了基于 LangSmith 的多 Agent 流水线评估框架，
   支持 14 个基准需求（分 4 个难度等级）、4 个自动化评估器
   （完成率/耗时/代码语法/护栏健康）和 LLM-as-Judge 质量评分。"
──
"""

import asyncio
import os
import time
from datetime import datetime

# ── 导入流水线入口函数 ──────────────────────────────────────────
# 使用 from main import pipeline as pipeline_fn 而非直接调用 main，
# 是为了避免 __name__ == "__main__" 分支的执行。
# 如果 langsmith 已安装，pipeline_fn 会被 @traceable 装饰，
# 从而让 benchmark 的评估调用自动形成追踪链路：
#   evaluate() → target_fn() → pipeline_fn() → run_agent() → agent.run()
from main import pipeline as pipeline_fn

# ═════════════════════════════════════════════════════════════════
# 基准测试需求数据集
# ═════════════════════════════════════════════════════════════════
# 共 14 个需求，按复杂度分为 4 个难度等级（Tier 1~4）：
#   Tier 1: 基础 CLI 工具（5 个）—— 单功能，简单逻辑
#   Tier 2: 数据处理（3 个）—— 文件解析、格式转换
#   Tier 3: 文件工具（3 个）—— 文件系统操作，需要算法
#   Tier 4: 实用工具（3 个）—— 多种模式，交互式逻辑
#
# 选择原则：
#   - 覆盖不同领域的编程任务
#   - 可独立验证（无需外部 API/数据库）
#   - 有明确的"完成"标准
#   - 难度梯度合理，可用于测量模型能力边界

BENCHMARK_REQUIREMENTS = [
    # ── Tier 1: 基础 CLI 工具 ──────────────────────────────────
    "开发 Python 命令行 Todo List，支持添加/查看/完成/删除任务，JSON 持久化",
    "开发 Python 计算器 CLI，支持加减乘除、历史记录、求平方根",
    "开发 Python 笔记管理 CLI，支持创建/查看/编辑/删除/搜索笔记，文件持久化",
    "开发 Python 密码生成器 CLI，支持指定长度/包含字符类型/批量生成",
    "开发 Python 文件组织工具 CLI，按扩展名自动分类整理目录文件",

    # ── Tier 2: 数据处理 ───────────────────────────────────────
    "开发 Python CSV 分析 CLI，读取 CSV 并输出每列统计信息（总数/均值/最大/最小/空值率），支持指定分隔符",
    "开发 Python JSON 转换器 CLI，将 JSON 文件转为 YAML/CSV/XML 格式，支持嵌套对象展平",
    "开发 Python 日志分析器 CLI，解析标准日志格式，支持按级别/时间范围过滤，输出统计摘要",

    # ── Tier 3: 文件工具 ───────────────────────────────────────
    "开发 Python 重复文件查找器 CLI，基于 MD5 哈希检测重复文件，支持按目录扫描/排除模式/软链接处理",
    "开发 Python 文件批量重命名 CLI，支持正则替换/序号追加/日期前缀/大小写转换，提供预览模式",
    "开发 Python 目录树打印 CLI，递归打印目录结构，支持 -L 深度限制/--exclude 排除模式/--output 导出",

    # ── Tier 4: 实用工具 ───────────────────────────────────────
    "开发 Python 进制转换器 CLI，支持二进制/八进制/十进制/十六进制互转，提供交互式模式",
    "开发 Python 计时器 CLI，支持倒计时/秒表/番茄钟模式，支持暂停/恢复/多轮次记录",
    "开发 Python 环境检查 CLI，检测系统信息（OS/Python版本/CPU内存/磁盘/环境变量），输出 JSON 报告",
]


# ═════════════════════════════════════════════════════════════════
# 当前方案：LangSmith Evaluation
# ═════════════════════════════════════════════════════════════════

def run_langsmith_evaluation(
    dataset_name: str = "dev-team-autogen-benchmark",
    max_requirements: int = 3,
    requirements: list[str] | None = None,
):
    """使用 LangSmith 运行自动化评估。

    这是当前推荐的评估方式，替代了旧的 BenchmarkRunner。

    执行流程：
      1. 创建/获取 LangSmith Dataset（从 BENCHMARK_REQUIREMENTS）
      2. 为每条需求创建 Example（包含输入和期望输出）
      3. 定义 target_fn：调用流水线函数，返回指标字典
      4. 调用 langsmith.evaluate()，传入 target 和 evaluators
      5. 结果上传到 LangSmith Dashboard，可在线查看

    Args:
        dataset_name: LangSmith Dataset 名称
        max_requirements: 最大运行需求数（控制时间和成本）
        requirements: 自定义需求列表，默认使用 BENCHMARK_REQUIREMENTS

    Usage:
        # 需要设置环境变量：
        #   LANGCHAIN_TRACING_V2=true
        #   LANGCHAIN_API_KEY=lsv2_...
        #
        from evaluation.benchmark import run_langsmith_evaluation
        run_langsmith_evaluation(max_requirements=5)
    """
    from langsmith import Client, evaluate as ls_evaluate

    from evaluation.evaluators import (
        code_validity_evaluator,
        completion_evaluator,
        duration_evaluator,
        guardrail_evaluator,
    )

    reqs = (requirements or BENCHMARK_REQUIREMENTS)[:max_requirements]
    client = Client()

    # ── 1. 创建/获取 Dataset ───────────────────────────────────
    # Dataset 是 LangSmith 的概念，一组输入-输出示例的集合。
    # 如果已存在则复用（upsert 语义），避免重复创建。
    try:
        dataset = client.create_dataset(
            dataset_name=dataset_name,
            description="dev-team-autogen pipeline benchmark requirements",
        )
    except Exception:
        dataset = client.read_dataset(dataset_name=dataset_name)

    # ── 2. 添加/更新 Examples ──────────────────────────────────
    # Example = 一条测试用例，包括 input（需求）和 output（期望结果）。
    # 期望结果用于 evaluator 计算评分（如 expected_phases=5）。
    existing = {e.inputs.get("requirement", "") for e in client.list_examples(dataset_id=dataset.id)}
    for req in reqs:
        if req not in existing:
            client.create_example(
                inputs={"requirement": req},
                outputs={"expected_phases": 5, "language": "Python", "tier": _tier_of(req)},
                dataset_id=dataset.id,
            )
            print(f"  [dataset] + {req[:60]}...")
        else:
            print(f"  [dataset]   {req[:60]}... (exists)")

    # ── 3. 定义目标函数 ────────────────────────────────────────
    # LangSmith evaluate() 会对每个 example 调用此函数。
    # 返回值被 evaluators 接收进行评估。
    def target_fn(inputs: dict) -> dict:
        req = inputs["requirement"]
        print(f"\n--- Evaluating: {req[:60]}... ---")
        return asyncio.run(pipeline_fn(req, benchmark_mode=True))

    # ── 4. 执行评估 ────────────────────────────────────────────
    # 传入 4 个评估器（在 evaluators.py 中定义）：
    #   completion_evaluator  — 流水线完成率
    #   duration_evaluator    — 执行耗时评分
    #   code_validity_evaluator — 生成代码的语法正确性
    #   guardrail_evaluator   — 安全护栏拦截率
    print(f"\nStarting LangSmith Evaluation ({len(reqs)} requirements)...")
    results = ls_evaluate(
        target_fn,
        data=dataset_name,
        evaluators=[
            completion_evaluator,
            duration_evaluator,
            code_validity_evaluator,
            guardrail_evaluator,
        ],
        experiment_prefix="dev-team-autogen",
        metadata={
            "pipeline": "dev-team-autogen",
            "num_agents": 4,
            "benchmark_set": dataset_name,
        },
    )
    print(f"LangSmith Evaluation complete. Results at: {results.url}")
    return results


# ═════════════════════════════════════════════════════════════════
# 遗留方案：自定义 BenchmarkRunner（已弃用）
#
# ⚠  弃用理由：
#     这个临时方案将指标写入 JSON 文件并需要手动比对。
#     LangSmith 免费提供了所有必需的能力：
#       - 结构化结果存储和版本对比
#       - Web Dashboard（无需解析 JSON 文件）
#       - 内置评估器和 LLM-as-Judge
#       - 人工审核和标注功能
#
#     保留此代码注释仅作为迁移路径的参考文档。
# ═════════════════════════════════════════════════════════════════

# 弃用代码：BenchmarkRunner 类定义已注释
# 迁移前后对比（20 行自定义代码 → 1 行 evaluate 调用）：
#   旧：BenchmarkRunner.run_all() — 手动循环、手动记录、手动汇总
#   新：langsmith.evaluate() — 自动迭代 Dataset、自动上传结果


# ── 辅助函数 ─────────────────────────────────────────────────────

def _tier_of(requirement: str) -> str:
    """根据需求文本中的关键词，将其分类到难度等级。

    用于在创建 LangSmith Example 时标注 tier 字段，
    方便在 Dashboard 中按难度层级筛选和对比结果。
    """
    tiers = {
        "CSV": "data", "JSON": "data", "日志": "data",
        "重复文件": "file", "重命名": "file", "目录树": "file",
        "进制": "utility", "计时器": "utility", "环境检查": "utility",
        "Todo": "basic", "计算器": "basic", "笔记": "basic", "密码": "basic", "文件组织": "basic",
    }
    for keyword, tier in tiers.items():
        if keyword in requirement:
            return tier
    return "other"


def sync_dataset(
    dataset_name: str = "dev-team-autogen-benchmark",
    requirements: list[str] | None = None,
):
    """将完整的 BENCHMARK_REQUIREMENTS 列表同步到 LangSmith Dataset。

    添加新需求后运行此函数即可更新 Dataset，无需重新创建。
    不会覆盖已有 examples（幂等操作）。

    Usage:
        python -c "from evaluation.benchmark import sync_dataset; sync_dataset()"
        # 或
        python evaluation/benchmark.py --sync-only
    """
    from langsmith import Client

    client = Client()
    reqs = requirements or BENCHMARK_REQUIREMENTS

    try:
        dataset = client.create_dataset(
            dataset_name=dataset_name,
            description="dev-team-autogen pipeline benchmark requirements (14 items, 4 tiers)",
        )
        print(f"Dataset '{dataset_name}' created")
    except Exception:
        dataset = client.read_dataset(dataset_name=dataset_name)
        print(f"Dataset '{dataset_name}' found")

    existing = {e.inputs.get("requirement", "") for e in client.list_examples(dataset_id=dataset.id)}
    added = 0
    for req in reqs:
        if req not in existing:
            client.create_example(
                inputs={"requirement": req},
                outputs={"expected_phases": 5, "language": "Python", "tier": _tier_of(req)},
                dataset_id=dataset.id,
            )
            added += 1
            print(f"  + {req[:70]}...")
    print(f"Done: {added} new, {len(reqs) - added} already existed")


# ── 入口点 ─────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    if "--sync-only" in sys.argv:
        sync_dataset()
    else:
        run_langsmith_evaluation()
