"""
链路追踪（Tracing）

提供本地文件追踪能力，作为 LangSmith 云端追踪的降级/补充方案。

对比 LangSmith：
  ┌──────────────┬───────────────────┬──────────────────────┐
  │              │ TraceLogger       │ LangSmith            │
  ├──────────────┼───────────────────┼──────────────────────┤
  │ 存储方式      │ 本地 JSON 文件    │ 云端 SaaS            │
  │ 采集方式      │ 手动调用 log()    │ @traceable 自动采集  │
  │ 日志结构      │ 扁平事件列表      │ 嵌套 span 树        │
  │ 可视化        │ 无（需自行解析）  │ Web Dashboard       │
  │ 外部依赖      │ 无               │ langsmith SDK + key │
  │ 离线可用      │ ✅               │ ❌                  │
  └──────────────┴───────────────────┴──────────────────────┘

双写策略：
  当 LangSmith 和 TraceLogger 同时启用时，每条 LLM 调用会同时
  产生两条追踪记录（云端 + 本地），互为冗余备份。
"""

import json
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional


@dataclass
class TraceEvent:
    """单次追踪事件。

    与 FlowGuard.AuditEvent 的区别：
      AuditEvent → 安全审计用途，记录所有操作（包括护栏拦截的）
      TraceEvent → 性能追踪用途，只记录实际发生的 LLM 调用
    """
    timestamp: str
    phase: str
    agent: str
    event_type: str
    content_preview: str
    duration_ms: float
    metadata: dict = field(default_factory=dict)


class TraceLogger:
    """本地链路追踪器——将事件序列持久化为 JSON 文件。

    每次 pipeline 运行创建一个 session，所有事件共享同一个 session_id。
    日志文件命名格式：trace_YYYYMMDD_HHMMSS.json

    典型用法（在 _run_agent_impl 中）：
      if tracer:
          tracer.log("PM", "PM", "llm_call", content, elapsed,
                      {"output_length": len(content)})

      # pipeline 结束时
      if tracer:
          tracer.log("pipeline", "system", "pipeline_complete", ...)
          tracer.save()
    """

    def __init__(self, trace_dir: str = "output/traces"):
        self.trace_dir = trace_dir
        self.events: list[TraceEvent] = []
        # session_id 在初始化时生成，标识一次独立的 pipeline 运行
        self._session_id = datetime.now().strftime("%Y%m%d_%H%M%S")

    def log(
        self,
        phase: str,
        agent: str,
        event_type: str,
        content: str,
        duration_ms: float,
        metadata: Optional[dict] = None,
    ):
        """记录一次追踪事件。

        phase:      流水线阶段（PM/Dev/QA/Fix/Regression/pipeline）
        agent:      Agent 名称（PM/Developer/QA/system）
        event_type: 事件类型（llm_call / pipeline_complete / guardrail_block）
        content:    事件内容（仅保存前 300 字符作为预览）
        duration_ms:执行耗时
        metadata:   附加元数据（如 output_length、completion_rate 等）
        """
        event = TraceEvent(
            timestamp=datetime.now().isoformat(),
            phase=phase,
            agent=agent,
            event_type=event_type,
            content_preview=content[:300],
            duration_ms=round(duration_ms, 1),
            metadata=metadata or {},
        )
        self.events.append(event)

    def save(self):
        """将当前 session 的所有事件序列化到 JSON 文件。

        文件结构：
          {
            "session_id": "20260101_143000",
            "events": [
              { "timestamp": "...", "phase": "PM", ... },
              { "timestamp": "...", "phase": "Dev", ... },
              ...
            ]
          }
        """
        os.makedirs(self.trace_dir, exist_ok=True)
        path = os.path.join(self.trace_dir, f"trace_{self._session_id}.json")
        data = {
            "session_id": self._session_id,
            "events": [asdict(e) for e in self.events],
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return path

    def get_llm_call_chain(self) -> list[dict]:
        """获取所有 LLM 调用事件的序列（按时间序）。
        
        可用于重建流水线的执行顺序和耗时分析。
        """
        return [asdict(e) for e in self.events if e.event_type == "llm_call"]

    def get_guardrail_events(self) -> list[dict]:
        """获取所有安全护栏相关的事件。"""
        return [asdict(e) for e in self.events if "guardrail" in e.event_type]
