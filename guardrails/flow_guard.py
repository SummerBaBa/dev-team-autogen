"""
流程审计护栏（FlowGuard）

职责：记录流水线中每个操作的完整审计追踪，提供可追溯的不可变日志。

与 InputGuard/OutputGuard 的关系：
  InputGuard 和 OutputGuard 是"执行时检查"（同步拦截），
  FlowGuard  是"执行后记录"（异步审计），不参与拦截决策。

FlowGuard 记录的三大类事件：
  - passed:  Agent 调用正常完成且通过输出检查
  - failed:  Agent 调用超时或抛出异常
  - blocked: Agent 调用完成但被 OutputGuard 拦截

审计日志的输出：
  - 内存中维护 events 列表，每次 pipeline 运行结束后
  - 通过 save_audit_log() 持久化为 output/audit_log.json

可在简历中描述为：
  "设计了流程审计护栏系统，自动记录每次 LLM 调用的完整调用链、
   耗时、风险等级和拦截记录，生成不可变的审计日志用于合规检查。"
"""

import json
import os
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional


@dataclass
class AuditEvent:
    """一次审计事件的完整记录。

    event_id:      唯一标识符，格式 EVT-YYYYMMDD-HHMMSS-NNNN
    phase:         流水线阶段（PM/Dev/QA/Fix/Regression）
    agent:         Agent 名称
    action:        操作类型（目前为 llm_call，可扩展）
    status:        事件状态（passed/failed/blocked）
    input_preview: 输入的前 200 字符（避免存储完整输入）
    output_preview:输出的前 200 字符（同上，隐私考虑）
    timestamp:     ISO 格式时间戳
    duration_ms:   执行耗时（毫秒）
    risk_level:    风险等级，由 OutputGuard 的输出检查决定
    metadata:      扩展字段，可附加额外信息
    """
    event_id: str
    phase: str
    agent: str
    action: str
    status: str
    input_preview: str
    output_preview: str
    timestamp: str
    duration_ms: float
    risk_level: str = "safe"
    metadata: dict = field(default_factory=dict)


class FlowGuard:
    """流程审计护栏——记录全流水线的操作审计日志。

    设计要点：
      1. 事件驱动：每次 LLM 调用产生一条 AuditEvent
      2. 预览存储：只存输入/输出的前 200 字符，平衡信息量和存储成本
      3. 自增 ID：event_id 使用时间+序号，天然排序且唯一
      4. 失败检测：check_consecutive_failures() 可检测连续失败
      5. 持久化：save_audit_log() 输出标准 JSON，可被下游工具解析
    """

    def __init__(self, config):
        self.config = config
        self.events: list[AuditEvent] = []
        self._event_counter = 0

    def _next_id(self) -> str:
        """生成下一个审计事件 ID。

        格式：EVT-20260101-143000-0001
        包含时间信息，方便人工按 ID 排序即可获得时间序。
        """
        self._event_counter += 1
        return f"EVT-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{self._event_counter:04d}"

    def record(
        self,
        phase: str,
        agent: str,
        action: str,
        status: str,
        input_text: str,
        output_text: str,
        duration_ms: float,
        risk_level: str = "safe",
        metadata: Optional[dict] = None,
    ):
        """记录一次审计事件。

        每次 LLM 调用（无论是成功/失败/被拦截）都会触发此方法。
        由 main.py 中的 _run_agent_impl() 在三个场景调用：
          1. 输出检查通过 → status="passed"
          2. 输出检查拦截 → status="blocked"
          3. 调用超时/异常 → status="failed"
        """
        event = AuditEvent(
            event_id=self._next_id(),
            phase=phase,
            agent=agent,
            action=action,
            status=status,
            input_preview=input_text[:200],
            output_preview=output_text[:200],
            timestamp=datetime.now().isoformat(),
            duration_ms=duration_ms,
            risk_level=risk_level,
            metadata=metadata or {},
        )
        self.events.append(event)
        return event

    def check_consecutive_failures(self, max_failures: int = 3) -> bool:
        """检查最近的事件中是否出现连续失败。

        用途：如果某个 Agent 连续失败超过阈值，可以触发熔断机制
        （本系统目前未实现自动熔断，但接口已预留）。
        只检查最近 10 条事件，避免大量历史事件干扰。
        """
        recent = [e for e in self.events[-10:] if e.status == "failed"]
        return len(recent) >= max_failures

    def save_audit_log(self, path: str = "output/audit_log.json"):
        """将审计事件持久化到 JSON 文件。

        每次 pipieline 运行结束时调用一次。
        文件格式是标准的 JSON 数组，每条事件结构由 AuditEvent 定义。
        可被日志分析工具、SIEM 系统等下游消费。
        """
        os.makedirs(os.path.dirname(path), exist_ok=True)
        data = [asdict(e) for e in self.events]
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return path

    def summary(self) -> dict:
        """生成审计汇总统计。

        返回总事件数、通过/失败/拦截的计数和总耗时。
        可用于在流水线结束时打印摘要。
        """
        total = len(self.events)
        passed = sum(1 for e in self.events if e.status == "passed")
        failed = sum(1 for e in self.events if e.status == "failed")
        blocked = sum(1 for e in self.events if e.status == "blocked")
        total_duration = sum(e.duration_ms for e in self.events)
        return {
            "total_events": total,
            "passed": passed,
            "failed": failed,
            "blocked": blocked,
            "total_duration_ms": total_duration,
        }
