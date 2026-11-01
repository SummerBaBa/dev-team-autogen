"""
安全护栏（Guardrails）模块

本系统实现了一个 三层安全护栏 架构，在 Agent 流水线的关键路径上
对输入、输出和全流程进行安全检查：

  ┌──────────────┐     ┌──────────────┐     ┌──────────────┐
  │ InputGuard   │ ──▶ │ Agent 执行   │ ──▶ │ OutputGuard  │
  │ (输入检查)    │     │ (LLM 调用)   │     │ (输出检查)    │
  └──────────────┘     └──────────────┘     └──────────────┘
                               │
                               ▼
                        ┌──────────────┐
                        │ FlowGuard    │
                        │ (全流程审计)  │
                        └──────────────┘

简历可描述：
  "设计并实现了三层安全护栏系统（输入过滤/输出审查/全流程审计），
   有效防御提示注入和密钥泄露，保障 LLM 应用的生产环境安全性。"
"""

from .input_guard import InputGuard, InputVerdict
from .output_guard import OutputGuard, OutputVerdict
from .flow_guard import FlowGuard, AuditEvent

__all__ = ["InputGuard", "InputVerdict", "OutputGuard", "OutputVerdict", "FlowGuard", "AuditEvent"]
