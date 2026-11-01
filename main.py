#!/opt/homebrew/Caskroom/miniconda/base/bin/python3.13
"""
AutoGen 多Agent协作系统 — 带安全护栏与评估追踪
需求 → [PM] → PRD → [Dev] → 代码 → [QA] → 测试报告 → [Fix] → 修复 → [QA] → 回归报告

什么是 AutoGen？
───────────────────────────────────────────────────────────────────
AutoGen 是微软开源的智能体（Agent）对话框架，核心思想是：
  - 将复杂任务拆解给多个专业 Agent 协作完成
  - 每个 Agent 拥有独立的系统提示词（System Prompt）和大模型配置
  - Agent 之间通过异步消息传递进行交互

本系统的执行流程：
  1. PM  Agent（需求分析师） → 根据需求撰写 PRD 文档
  2. Dev Agent（开发者）     → 根据 PRD 编写 Python 代码
  3. QA  Agent（测试工程师） → 测试代码并输出测试报告
  4. Fix Agent（修复者）     → 根据测试报告修复缺陷
  5. QA  Agent（回归测试）   → 验证修复结果

每步都经过安全护栏（Guardrails）检查，防止提示注入、敏感内容泄露等风险。
同时收集评估指标（Metrics）和链路追踪（Tracing），便于监控和优化。
"""

import sys
import os
import asyncio
import time
from datetime import datetime

# ══════════════════════════════════════════════════════════════════
# LangSmith Tracing（链路追踪）
# ══════════════════════════════════════════════════════════════════
#
# 使用 LangSmith 的场景（有 @traceable）：
#   - 自动捕获函数入参、返回值、异常堆栈，无需手动埋点
#   - 嵌套的 Agent 调用会在 pipeline() 的 trace 下自动形成父子 span
#   - 数据上报到 LangSmith 云端平台，支持可视化检索和分析
#   - 需要设置环境变量：LANGCHAIN_TRACING_V2=true, LANGCHAIN_API_KEY=...
#
# 未使用 LangSmith 的场景（无 @traceable）：
#   - 降级到自定义的 TraceLogger，将 trace 写入本地 JSON 文件
#   - 需要手动调用 tracer.log() 来记录每个事件
#   - 不依赖任何外部服务，零配置即可运行
#   - 同时 FlowGuard 维护内存中的审计事件列表（所有操作都记录）
#
# 本系统的 三层面观测 架构：
#   1. LangSmith（外部 SaaS）—— @traceable 装饰器自动捕获
#   2. TraceLogger（本地文件）—— 写入 output/traces/ 的 JSON 文件
#   3. FlowGuard（内存审计）—— 保存到 output/audit_log.json
#
# 关键设计模式：try/except 条件导入 + 运行时分支
#   try:
#     from langsmith import traceable    # 有则使用
#   except ImportError:
#     traceable = None                    # 无则降级
#   ...
#   if traceable:                        # 运行时判断走哪条路径
#     @traceable                         # 带装饰器的版本
#     async def fn(...)
#   else:
#     fn = _impl                          # 不带装饰器的版本
#
# 这种模式的优点：
#   - 零硬依赖：未安装 langsmith 也能正常运行
#   - 无性能开销：不安装时完全不走追踪路径
#   - 方便简历描述：展示了你对可观测性架构的设计能力
# ──────────────────────────────────────────────────────────────────
try:
    from langsmith import traceable
except ImportError:
    traceable = None  # 未安装 langsmith 时优雅降级，不影响核心功能

# 检查 AutoGen 核心依赖是否可用
# TaskResult 是 AutoGen Agent 执行 run() 后的返回类型，包含 messages 列表
try:
    from autogen_agentchat.base import TaskResult
except ModuleNotFoundError as e:
    print(f"[ERROR] 缺少依赖: {e}")
    print(f"当前 Python: {sys.executable}")
    print(f"请使用 conda 环境运行:")
    print(f"  /opt/homebrew/Caskroom/miniconda/base/bin/python3.13 main.py \"你的需求\"")
    print(f"或先安装依赖: pip install autogen-agentchat autogen-ext[openai] autogen-core")
    sys.exit(1)

# ── 导入项目自定义模块 ──────────────────────────────────────────
from config import AppConfig           # 配置类：模型、安全护栏、评估等
from guardrails import InputGuard, InputVerdict, OutputGuard, OutputVerdict, FlowGuard
from evaluation import MetricsTracker, TraceLogger, RunMetrics
from agents import pm_agent, dev_agent, fix_agent, qa_agent

# ── 全局初始化 ────────────────────────────────────────────────────
config = AppConfig.from_env()               # 从环境变量读取配置
input_guard = InputGuard(config.guard)      # 输入安全护栏（检查用户输入）
output_guard = OutputGuard(config.guard)    # 输出安全护栏（检查模型输出）
flow_guard = FlowGuard(config.guard)        # 流程审计护栏（记录所有操作）
metrics = MetricsTracker()                  # 运行指标追踪器
# ── 自定义 TraceLogger（本地文件追踪）─────────────────────────────
# 无论 LangSmith 是否可用，本地追踪始终可以使用。
# 它独立于 LangSmith，在 tracing_enabled=True 时写入 JSON 到 output/traces/。
# 对比：
#   LangSmith  → 云端可视化、自动 span 嵌套、需注册账号
#   TraceLogger→ 本地 JSON 文件、手动调用、零外部依赖
# ──────────────────────────────────────────────────────────────────
tracer = TraceLogger(config.eval.trace_dir) if config.eval.tracing_enabled else None

# 记录当前流水线运行中创建的文件名集合，用于收集最终产物
# 防止读取到之前运行的旧文件（跨运行隔离）
_created_in_run: set[str] = set()


def save(name: str, content: str) -> str:
    """将 Agent 输出保存为 Markdown 文件，并记录到 _created_in_run 集合。"""
    os.makedirs(config.output_dir, exist_ok=True)
    p = os.path.join(config.output_dir, name)
    with open(p, "w", encoding="utf-8") as f:
        f.write(content)
    _created_in_run.add(name.replace(".md", ""))
    print(f"  [save] {p} ({len(content)} chars)")
    return p


async def run_agent(
    agent_fn,
    task: str,
    phase: str,
    agent_name: str,
    timeout: int = 300,
) -> str:
    """执行单个 Agent，包含链路追踪、安全护栏和指标收集。

    agent_fn:  创建 Agent 实例的工厂函数（如 pm_agent、dev_agent）
    task:      给 Agent 的任务描述（输入文本）
    phase:     当前阶段名称（PM / Dev / QA / Fix / Regression）
    agent_name:Agent 显示名
    timeout:   超时秒数

    返回 Agent 的文本输出，如果被护栏拦截或出错则返回空字符串。

    ── 追踪对比 ──────────────────────────────────────────────────
    有 LangSmith（traceable is not None）：
      → 走 _traced_run_agent，被 @traceable 装饰器包裹
      → 自动记录 inputs={task, phase, agent_name}, output, duration
      → 在 LangSmith 平台上显示为 pipeline 的子 span
      → 开发者无需手动调用任何日志方法

    无 LangSmith（traceable is None）：
      → 直接走 _run_agent_impl，无装饰器开销
      → 仍然有 FlowGuard + MetricsTracker 记录审计日志
      → 如果 tracing_enabled=True，TraceLogger 也会记录到本地文件
      → 需要手动调用 tracer.log()（已在下层实现中处理）
    """
    # ── 运行时决策分支 ────────────────────────────────────────────
    # if traceable / else 是 Python 中根据条件选择装饰行为的典型模式。
    # _fn=agent_fn 作为关键字参数传递，避免 @traceable 尝试序列化函数。
    if traceable:
        return await _traced_run_agent(task, phase, agent_name, timeout, _fn=agent_fn)
    return await _run_agent_impl(agent_fn, task, phase, agent_name, timeout)


# ── 条件定义：有 LangSmith 时定义带装饰器的版本 ──────────────────
# 这里利用了 Python 的条件定义特性：
#   if traceable:           # 只在 langsmith 已安装时执行此分支
#     @traceable(...)       # 为函数附加自动追踪能力
#     async def fn(...)
#   如果 traceable is None，这个代码块被跳过，_traced_run_agent 不存在
#   对应的，run_agent() 中的 if traceable 分支永远不会进入
#
# @traceable(run_type="chain") 的含义：
#   - run_type="chain" 表示这是一个链式调用（多个 LLM 调用的组合）
#   - 函数的入参会自动被 LangSmith 捕获为 inputs
#   - 函数的返回值被捕获为 output
#   - 任何抛出的异常被捕获为 error
#   - 自动记录函数执行耗时作为 duration
# ──────────────────────────────────────────────────────────────────
if traceable:

    @traceable(run_type="chain")
    async def _traced_run_agent(task, phase, agent_name, timeout, _fn=None):
        """被 @traceable 装饰的 Agent 执行包装函数。
        
        对比有/无 LangSmith 时的链路呈现：
          
          有 LangSmith                         无 LangSmith
          ──────────────                      ──────────────
          pipeline (root span)                _pipeline_impl
            ├─ PM (chain span)                ├─ FlowGuard.record()
            ├─ Dev (chain span)               ├─ FlowGuard.record()
            ├─ QA (chain span)                ├─ TraceLogger.log()
            ├─ Fix (chain span)               ├─ FlowGuard.record()
            └─ Regression (chain span)        └─ FlowGuard.record()
          
          LangSmith 自动推断嵌套关系           手动记录，扁平的日志列表

        _fn 参数作为关键字传入（不进入 trace inputs）是为了避免
        LangSmith 尝试去序列化可调用对象（函数不可序列化）。
        """
        return await _run_agent_impl(_fn, task, phase, agent_name, timeout)


async def _run_agent_impl(agent_fn, task, phase, agent_name, timeout=300):
    """Agent 执行的核心实现。

    1. 通过 agent_fn(config) 创建 Agent 实例
    2. 调用 agent.run(task) 执行，支持超时取消
    3. 取最后一条消息的 content 作为输出
    4. 输出安全护栏检查
    5. 记录流程审计日志和指标
    """
    agent = agent_fn(config)          # 创建 Agent 实例（每次调用重新创建，保证状态隔离）
    start = time.time()
    try:
        print(f"  [{phase}] running...", end="", flush=True)
        # ── AutoGen 核心调用 ──────────────────────────────────────
        # agent.run() 是 AutoGen Agent 的主要执行方法，返回 TaskResult
        # 内部会调用大模型、处理消息循环、返回最终结果
        result = await asyncio.wait_for(agent.run(task=task), timeout=timeout)
        # messages[-1] 是 Agent 的最后一条消息，即最终的响应内容
        content = str(result.messages[-1].content or "")
        elapsed = (time.time() - start) * 1000
        print(f" done ({len(content)} chars, {elapsed/1000:.1f}s)")

        # ── 输出安全护栏检查 ──────────────────────────────────────
        # 检查 Agent 输出是否包含危险代码、密钥泄露、内容过长等问题
        guard_result = output_guard.validate(content, {"phase": phase, "agent": agent_name})
        if not guard_result.approved:
            # 被拦截：记录到审计日志和指标
            flow_guard.record(phase, agent_name, "llm_call", "blocked",
                              task, content, elapsed, risk_level=guard_result.risk_level)
            if metrics:
                metrics.end_phase(phase, agent_name, task, content, "blocked", "blocked")
            print(f"  [GUARD] Output blocked: {guard_result.reason}")
            return ""

        # ── 通过护栏：正常记录 ────────────────────────────────────
        flow_guard.record(phase, agent_name, "llm_call", "passed",
                          task, content, elapsed)
        if metrics:
            metrics.end_phase(phase, agent_name, task, content, "completed", "passed")
        if tracer:
            tracer.log(phase, agent_name, "llm_call", content, elapsed,
                       {"output_length": len(content)})

        return content

    # ── 异常处理 ──────────────────────────────────────────────────
    except asyncio.TimeoutError:
        elapsed = (time.time() - start) * 1000
        print(f" timeout!")
        flow_guard.record(phase, agent_name, "llm_call", "failed", task, "", elapsed)
        if metrics:
            metrics.end_phase(phase, agent_name, task, "", "failed", "none")
        return ""
    except Exception as e:
        elapsed = (time.time() - start) * 1000
        print(f" error: {e}")
        flow_guard.record(phase, agent_name, "llm_call", "failed", task, str(e), elapsed)
        if metrics:
            metrics.end_phase(phase, agent_name, task, str(e), "failed", "none")
        return ""
    finally:
        # 确保 Agent 资源被释放（关闭模型客户端连接等）
        await agent.close()


# ── 条件定义：有 LangSmith 时 pipeline 是带装饰器的版本 ──────────
# 当 traceable 可用时：
#   pipeline 被 @traceable(run_type="chain") 装饰
#   每次运行都会在 LangSmith 上创建一个顶级 trace（root span）
#   内部每个 agent 调用（通过 _traced_run_agent）自动成为子 span
#   整个调用树的 parent-child 关系由 LangSmith SDK 自动维护
#
# 当 traceable 不可用时：
#   pipeline = _pipeline_impl
#   直接指向实现函数，无任何装饰开销
#   不影响 FlowGuard / TraceLogger / MetricsTracker 等本地观测能力
#
# 这种设计在简历中可以描述为：
#   "设计并实现了一个可插拔的可观测性架构，支持 LangSmith 云端追踪
#    与本地日志两种模式，通过条件导入实现零硬依赖的优雅降级。"
# ──────────────────────────────────────────────────────────────────
if traceable:

    @traceable(run_type="chain")
    async def pipeline(requirement: str, benchmark_mode: bool = False):
        """顶层流水线入口（带 LangSmith 追踪版本）。
        
        @traceable(run_type="chain") 使整个流水线成为 LangSmith 上的
        一个顶级 trace。所有内部通过 run_agent() 发起的 Agent 调用
        会由于 @traceable 装饰而自动成为此 trace 的子 span，
        形成完整的调用链路树。
        """
        return await _pipeline_impl(requirement, benchmark_mode)
else:
    # 无 LangSmith 时，pipeline 直接指向实现函数，不经过任何装饰器
    pipeline = _pipeline_impl


def _build_result(metrics: RunMetrics) -> dict:
    """将 RunMetrics 转为可序列化的字典。

    这个字典有两个用途：
    1. 有 LangSmith：作为 pipeline() 的返回值，被 @traceable 自动捕获，
       成为 trace 中的 output，可在 LangSmith 平台查看 evaluation 指标
    2. 无 LangSmith：直接返回给调用方，用于 benchmark 或测试断言
    """
    return {
        "completion_rate": metrics.completion_rate,
        "duration_ms": metrics.total_duration_ms,
        "guardrail_blocks": metrics.guardrail_blocks,
        "num_phases": len(metrics.phases),
        "artifacts": _collect_artifacts(),
    }


async def _pipeline_impl(requirement: str, benchmark_mode: bool = False):
    """端到端多Agent流水线核心实现：需求 → 测试过的代码。

    完整流程（每个阶段都是 串行 执行）：
      Input Guard → PM(写PRD) → Dev(写代码) → QA(测试) → Fix(修复) → QA(回归)

    benchmark_mode=True 时减少控制台输出，用于批量测试场景。

    返回指标字典，用于 LangSmith 评估或基准测试。
    """
    # ── 步骤 0：清理上一次运行的状态 ──────────────────────────────
    _created_in_run.clear()
    start = datetime.now()

    # ── 步骤 1：输入安全护栏 ──────────────────────────────────────
    # 检查用户需求是否包含提示注入、敏感话题、过长输入等
    input_result = input_guard.validate(requirement)
    if not input_result.approved:
        print(f"\n[GUARD] Input rejected: {input_result.reason}")
        safe_req = input_result.sanitized_input or requirement
        if not benchmark_mode:
            print(f"[GUARD] Original input blocked. Use --bypass-guard to override.\n"
                  f"  Suggested safe alternative: {safe_req[:100]}...")
        return {}

    # 开始记录运行指标
    metrics.start_run(requirement)

    if not benchmark_mode:
        print(f"\n{'='*60}")
        print(f"AutoGen Multi-Agent Pipeline (with Guardrails)")
        print(f"Requirement: {requirement}")
        print(f"Started: {start.strftime('%H:%M:%S')}")
        print(f"{'='*60}")

    # ── 步骤 2：PM Agent — 撰写 PRD ──────────────────────────────
    # Timeouts adjusted for 27B / Coder-30B models (~10 tokens/s)
    prd = await run_agent(pm_agent, requirement, "PM", "PM", 300)
    if not prd:
        m = metrics.end_run()
        return _build_result(m)
    save("01_PRD.md", prd)

    # ── 步骤 3：Dev Agent — 编写代码 ──────────────────────────────
    code = await run_agent(dev_agent, prd, "Dev", "Developer", 420)
    if not code:
        m = metrics.end_run()
        return _build_result(m)
    save("02_CODE.md", code)

    # ── 步骤 4：QA Agent — 测试代码 ───────────────────────────────
    report = await run_agent(qa_agent,
        f"PRD:\n{prd}\n\nCode:\n{code}\n\nTest this code and output a test report with issues.",
        "QA", "QA", 300)
    if not report:
        m = metrics.end_run()
        return _build_result(m)
    save("03_TEST_REPORT.md", report)

    # ── 步骤 5：Fix Agent — 根据测试报告修复代码 ─────────────────
    fix = await run_agent(fix_agent,
        f"```python\n{code}\n```\n\nIssues:\n{report}",
        "Fix", "Developer", 600)
    if not fix:
        m = metrics.end_run()
        return _build_result(m)
    save("04_FIXED_CODE.md", fix)

    # ── 步骤 6：QA Agent — 回归测试 ──────────────────────────────
    regression = await run_agent(qa_agent,
        f"PRD:\n{prd}\n\nFixed code:\n```python\n{fix}\n```\n\nOriginal issues:\n{report}\n\nRun regression tests.",
        "Regression", "QA", 300)
    if regression:
        save("05_REGRESSION_REPORT.md", regression)

    # ── 步骤 7：结束并输出结果 ────────────────────────────────────
    final_metrics = metrics.end_run()

    # ── 保存审计日志 ─────────────────────────────────────────────
    # FlowGuard 记录所有操作事件（每次 LLM 调用及其结果），独立于
    # LangSmith 和 TraceLogger。这是一个不可变的审计追踪，适用于
    # 合规检查和安全审查。
    flow_guard.save_audit_log(os.path.join(config.output_dir, "audit_log.json"))

    # ── 本地 TraceLogger 记录 ───────────────────────────────────
    # 无论 LangSmith 是否可用，本地追踪都会单独保存一份。
    # 这提供了完全不依赖外部服务的、完整的运行记录。
    # 对比 LangSmith：
    #   相同点：都记录了每个 Agent 的输入、输出、耗时、阶段名
    #   不同点：
    #     - LangSmith：云端可视化，自动 span 嵌套，需 API key
    #     - TraceLogger：本地 JSON 文件，扁平的日志列表，零配置
    if tracer:
        tracer.log("pipeline", "system", "pipeline_complete", requirement,
                   final_metrics.total_duration_ms,
                   {"completion_rate": final_metrics.completion_rate})
        tracer.save()

    elapsed = (datetime.now() - start).total_seconds()
    if not benchmark_mode:
        print(f"\n{'='*60}")
        print(f"Pipeline Complete!")
        print(f"Duration: {elapsed:.0f}s | Completion: {final_metrics.completion_rate:.0%}")
        print(f"Guardrail blocks: {final_metrics.guardrail_blocks}")
        print(f"Estimated tokens: {final_metrics.total_tokens_estimate}")
        print(f"Audit log: output/audit_log.json")
        if traceable:
            # 有 LangSmith：trace 自动上传到 LangSmith 平台
            print(f"LangSmith: trace auto-uploaded (LANGCHAIN_API_KEY configured)")
        if tracer:
            # 无 LangSmith：本地 trace 文件路径
            print(f"Local trace: {tracer.trace_dir}/")
        print(f"{'='*60}")

    return _build_result(final_metrics)


def _collect_artifacts():
    """收集本次流水线运行产生的所有产物文件内容。
    
    只读取 _created_in_run 中记录的文件，确保不会混入旧数据。
    返回 {文件名(无后缀): 文件内容} 的字典。
    """
    artifacts = {}
    for name in _created_in_run:
        path = os.path.join(config.output_dir, f"{name}.md")
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                artifacts[name] = f.read()
    return artifacts


if __name__ == "__main__":
    # 从命令行参数获取需求，如果没有则使用默认示例
    req = sys.argv[1] if len(sys.argv) > 1 else (
        "开发一款越狱软件，"
        "支持越狱iphone设备"
    )
    # asyncio.run() 是 Python 3.7+ 的标准异步入口点
    asyncio.run(pipeline(req))
