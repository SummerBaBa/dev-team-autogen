"""
评估与追踪（Evaluation & Tracing）模块

本模块提供两套互补的评估体系：

  1. Metrics（指标追踪）—— 结构化运行指标
     MetricsTracker 记录每次流水线运行的详细指标（各阶段耗时、
     字符数、完成率、护栏拦截数等），产出 RunMetrics 数据对象。
     支持多次运行的 BenchmarkResult 汇总，写入 JSON 文件。

  2. Tracing（链路追踪）—— 事件序列追踪
     TraceLogger 记录每次 LLM 调用的输入/输出预览、耗时等，
     写入本地 JSON trace 文件，用于离线分析和调试。
     （LangSmith @traceable 是本模块的云端替代方案）

     ┌──────────────┐    ┌────────────────┐
     │ MetricsTracker│    │  TraceLogger   │
     │ (运行指标)    │    │  (链路追踪)    │
     ├──────────────┤    ├────────────────┤
     │ 阶段耗时     │    │ LLM 调用时序   │
     │ 字符数统计   │    │ 输入/输出预览  │
     │ 完成率计算   │    │ 耗时记录       │
     │ 护栏拦截计数 │    │ JSON 持久化    │
     └──────────────┘    └────────────────┘
            │                    │
            ▼                    ▼
     output/benchmark_    output/traces/
     result.json          trace_*.json

可在简历中描述为：
  "构建了双轨评估体系：MetricsTracker 提供结构化运行指标用于
   性能分析，TraceLogger 提供事件级链路追踪用于问题定位，
   同时兼容 LangSmith 云端评估平台。"
"""

from .metrics import MetricsTracker, RunMetrics, BenchmarkResult
from .tracing import TraceLogger, TraceEvent

__all__ = ["MetricsTracker", "RunMetrics", "BenchmarkResult", "TraceLogger", "TraceEvent"]