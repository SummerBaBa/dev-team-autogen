"""
运行指标追踪（Metrics）

使用三层数据模型来度量流水线的运行质量：

  PhaseMetrics    → 一次 Agent 调用的指标（粒度：单次 LLM 调用）
  RunMetrics      → 一次完整流水线的指标（粒度：一次需求开发）
  BenchmarkResult → 多次流水线的汇总指标（粒度：批量评估）

关键指标说明：
  - completion_rate: 阶段完成率（0~1），流水线前期失败会拉低此值
  - total_tokens_estimate: token 估算（字符数之和，非精确 token 计数）
  - guardrail_blocks: 安全护栏拦截次数，反映模型输出的风险程度
  - avg_phase_duration_ms: 各阶段平均耗时，用于识别性能瓶颈
"""

import json
import os
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional


@dataclass
class PhaseMetrics:
    """单阶段（一次 Agent 调用）的运行指标。

    phase:           阶段名（PM/Dev/QA/Fix/Regression）
    agent:           Agent 角色名
    duration_ms:     该阶段执行耗时（毫秒）
    input_chars:     输入的字符数
    output_chars:    输出的字符数
    status:          执行状态（completed/failed/blocked）
    guardrail_result:输出护栏检查结果（passed/blocked/none）
    """
    phase: str
    agent: str
    duration_ms: float
    input_chars: int
    output_chars: int
    status: str
    guardrail_result: str = "none"


@dataclass
class RunMetrics:
    """单次完整流水线运行（一次需求开发）的汇总指标。

    completion_rate（属性）:
      计算方式：completed 阶段数 / 总阶段数
      意义：衡量流水线在多阶段串联执行中的稳定性。
      理想值 1.0（全部 5 阶段完成），早期失败会显著降低此值。

    all_artifacts_created:
      当 completion_rate == 1.0 时为 True，表示所有 5 个产物文件
      （PRD/代码/测试报告/修复代码/回归报告）均已生成。

    total_tokens_estimate:
      基于字符数的 token 估算（输入 + 输出）。
      这不是精确的 token 计数，但足以用于成本占比分析。
    """
    requirement: str
    timestamp: str
    total_duration_ms: float
    total_tokens_estimate: int
    phases: list[PhaseMetrics] = field(default_factory=list)
    guardrail_blocks: int = 0
    all_artifacts_created: bool = False

    @property
    def completion_rate(self) -> float:
        """计算流水线完成率。

        如果 5 个阶段全部完成，completion_rate = 1.0。
        如果前 3 个阶段成功、第 4 阶段失败，completion_rate = 0.75。
        无任何阶段时返回 0.0（通常意味着输入护栏拦截）。
        """
        if not self.phases:
            return 0.0
        completed = sum(1 for p in self.phases if p.status == "completed")
        return completed / len(self.phases)

    @property
    def avg_phase_duration_ms(self) -> float:
        """各阶段的平均耗时。

        可用于识别瓶颈阶段——如果某个阶段耗时显著高于其他，
        可能需要调整该 Agent 的模型或提示词。
        """
        if not self.phases:
            return 0.0
        durations = [p.duration_ms for p in self.phases]
        return sum(durations) / len(durations)


@dataclass
class BenchmarkResult:
    """多次 Benchmark 运行的汇总结果。

    用于：
      1. 评估模型切换后的性能变化
      2. 检测提示词优化是否带来了实质改进
      3. 生成持续集成（CI）中的质量门禁指标

    save() 方法输出标准格式的 JSON 文件，可与历史结果对比。
    """
    runs: list[RunMetrics] = field(default_factory=list)
    benchmark_time: str = ""

    def add_run(self, metrics: RunMetrics):
        self.runs.append(metrics)

    @property
    def avg_completion_rate(self) -> float:
        if not self.runs:
            return 0.0
        return sum(r.completion_rate for r in self.runs) / len(self.runs)

    @property
    def avg_duration_ms(self) -> float:
        if not self.runs:
            return 0.0
        return sum(r.total_duration_ms for r in self.runs) / len(self.runs)

    @property
    def total_guardrail_blocks(self) -> int:
        return sum(r.guardrail_blocks for r in self.runs)

    def save(self, path: str = "output/benchmark_result.json"):
        """将 Benchmark 结果持久化为 JSON 文件。

        格式：
          {
            "benchmark_time": "2026-01-01T14:30:00",
            "summary": {
              "num_runs": 5,
              "avg_completion_rate": 0.85,
              "avg_duration_ms": 123456,
              "total_guardrail_blocks": 2
            },
            "runs": [ ... ]  // 每条 RunMetrics 的序列化
          }
        """
        os.makedirs(os.path.dirname(path), exist_ok=True)
        data = {
            "benchmark_time": self.benchmark_time,
            "summary": {
                "num_runs": len(self.runs),
                "avg_completion_rate": self.avg_completion_rate,
                "avg_duration_ms": self.avg_duration_ms,
                "total_guardrail_blocks": self.total_guardrail_blocks,
            },
            "runs": [asdict(r) for r in self.runs],
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return path


class MetricsTracker:
    """运行指标追踪器——负责收集单次流水线的运行数据。

    生命周期（与 pipeline 一一对应）：
      1. start_run(requirement)  → 在 pipeline 开始时调用
      2. end_phase(...)          → 在每个 Agent 调用完成后调用
      3. end_run()               → 在 pipeline 结束时调用，返回 RunMetrics

    使用模式（由 main.py 的 _run_agent_impl 和 _pipeline_impl 驱动）：
      tracker = MetricsTracker()
      tracker.start_run("开发 Todo List")
      # ... Agent 调用 ...
      tracker.end_phase("PM", "PM", input, output, "completed", "passed")
      # ... 更多 Agent 调用 ...
      run_metrics = tracker.end_run()
      print(run_metrics.completion_rate)  # 0.8
    """

    def __init__(self):
        self._current_run: Optional[RunMetrics] = None
        self._phase_start: Optional[float] = None

    def start_run(self, requirement: str):
        """开始一次新的运行。

        重置状态并创建新的 RunMetrics 对象。
        requirement 截取前 100 字符以防止指标数据膨胀。
        """
        self._current_run = RunMetrics(
            requirement=requirement[:100],
            timestamp=datetime.now().isoformat(),
            total_duration_ms=0.0,
            total_tokens_estimate=0,
        )
        self._run_start = time.time()

    def start_phase(self):
        """标记阶段开始（计时起点）。"""
        self._phase_start = time.time()

    def end_phase(
        self,
        phase: str,
        agent: str,
        input_text: str,
        output_text: str,
        status: str,
        guardrail_result: str = "none",
    ):
        """记录一个阶段的完成指标。

        由 _run_agent_impl 在 Agent 执行完成后调用。
        如果 guardrail_result == "blocked"，会自动递增 guardrail_blocks 计数。
        """
        if self._current_run is None:
            return
        duration = (time.time() - self._phase_start) * 1000 if self._phase_start else 0
        self._current_run.phases.append(PhaseMetrics(
            phase=phase,
            agent=agent,
            duration_ms=round(duration, 1),
            input_chars=len(input_text),
            output_chars=len(output_text),
            status=status,
            guardrail_result=guardrail_result,
        ))
        if guardrail_result == "blocked":
            self._current_run.guardrail_blocks += 1

    def end_run(self) -> RunMetrics:
        """结束本次运行，计算汇总指标并返回。

        计算总耗时、估算 token 数、以及是否所有产物均已创建。
        返回 RunMetrics 后，内部状态重置为 None（防止重复调用）。
        """
        if self._current_run is None:
            raise RuntimeError("No active run")
        self._current_run.total_duration_ms = round(
            (time.time() - self._run_start) * 1000, 1
        ) if self._run_start else 0
        self._current_run.total_tokens_estimate = sum(
            p.input_chars + p.output_chars for p in self._current_run.phases
        )
        self._current_run.all_artifacts_created = (
            self._current_run.completion_rate == 1.0
        )
        run = self._current_run
        self._current_run = None
        return run
