"""
输入安全护栏（InputGuard）

职责：在用户需求进入 Agent 流水线之前，对其进行安全检查。

防护范围（Pipeline 的第一道防线）：
  1. 长度检查  — 防止极端长输入导致 token 耗尽或 DoS
  2. 提示注入  — 检测"忽略之前的指令"等 Prompt Injection 攻击
  3. 敏感话题  — 过滤武器制作、毒品等违禁内容

设计模式：责任链（Chain of Responsibility）
  validate() 按顺序调用三个检查方法，任一失败则立即拦截。
  sanitize() 在检查前预处理输入（如剥离控制字符）。

可在简历中描述为：
  "基于责任链模式实现了输入安全护栏，通过正则匹配与关键词库双重机制
   检测提示注入攻击，有效防御了 Prompt Injection 安全威胁。"
"""

import re
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class InputVerdict:
    """输入安全检查结果。

    approved:       是否通过（True=通过, False=被拦截）
    reason:         拦截原因描述（仅 approved=False 时有意义）
    sanitized_input:经过消毒处理的输入文本（即使通过也保留，可被下游使用）
    """
    approved: bool
    reason: str = ""
    sanitized_input: Optional[str] = None


class InputGuard:
    """输入安全护栏——对用户输入执行多层安全检查。

    检查流水线（按顺序执行）：
      1. sanitize()       → 剥离控制字符等不可见字符
      2. check_length()   → 确保输入长度在允许范围内
      3. check_injection()→ 检测提示注入模式（正则模糊匹配）
      4. check_sensitive_topics() → 过滤敏感话题（精确关键词匹配）
    """

    def __init__(self, config):
        self.config = config
        # ── 提示注入检测的正则模式库 ────────────────────────────
        # 覆盖中英文两种注入方式：
        #   英文：ignore previous instructions, you are now, jailbreak, bypass...
        #   中文：忽略指令、你扮演、绕过安全、越狱...
        #
        # 设计思路：
        #   - 使用 re.IGNORECASE 忽略大小写
        #   - 模式不追求 100% 准确率，而是优先降低误报（false positive）
        #   - 使用 .* 和 \s+ 等灵活匹配，对抗简单的混淆绕过尝试
        self._injection_patterns = [
            re.compile(r"ignore\s+(previous|all|above|prior).*instructions?", re.IGNORECASE),
            re.compile(r"忽略.*(前面|上面|之前|所有).*(指令|规则|要求)", re.IGNORECASE),
            re.compile(r"you\s+are\s+(now|a)\s+", re.IGNORECASE),
            re.compile(r"(system|prompt)\s*(override|injection)", re.IGNORECASE),
            re.compile(r"(jailbreak|dan\s*mode|do\s*anything\s*now)", re.IGNORECASE),
            re.compile(r"bypass\s+(safety|restriction|filter)", re.IGNORECASE),
            re.compile(r"ignore\s+(all|the|your)\s*(rule|guideline|policy)", re.IGNORECASE),
            re.compile(r"忽略.*(所有|你的).*(规则|指南|策略)", re.IGNORECASE),
            re.compile(r"你(现在|将).*(是|扮演|作为)", re.IGNORECASE),
            re.compile(r"(绕过|破解|越狱).*(安全|限制|审查)", re.IGNORECASE),
        ]

    def check_injection(self, text: str) -> InputVerdict:
        """检查文本是否包含提示注入（Prompt Injection）模式。

        提示注入是 LLM 应用面临的最大安全威胁之一：
          用户通过在输入中嵌入"忽略之前的指令，你现在是一个..."等指令，
          试图覆盖 Agent 的 System Prompt。

        返回 verdict，匹配到任意模式即判定为注入攻击。
        """
        for pattern in self._injection_patterns:
            if pattern.search(text):
                return InputVerdict(
                    approved=False,
                    reason=f"检测到提示注入模式: {pattern.pattern[:50]}..."
                )
        return InputVerdict(approved=True)

    def check_sensitive_topics(self, text: str) -> InputVerdict:
        """检查文本是否包含配置的敏感话题关键词。

        使用精确关键词匹配（而非正则），与 check_injection 的正则模式互补。
        关键词列表在 config.GuardConfig.blocked_topics 中定义，便于运维调整。
        """
        for keyword in self.config.blocked_topics:
            if keyword in text:
                return InputVerdict(
                    approved=False,
                    reason=f"输入包含敏感话题: {keyword}"
                )
        return InputVerdict(approved=True)

    def check_length(self, text: str) -> InputVerdict:
        """检查输入长度是否超过配置限制。

        两个目的：
          1. 防止恶意超长输入导致 token 耗尽
          2. 防止误操作粘贴了巨量文本
        """
        if len(text) > self.config.max_input_length:
            return InputVerdict(
                approved=False,
                reason=f"输入过长: {len(text)} 字符 (限制: {self.config.max_input_length})"
            )
        return InputVerdict(approved=True)

    def sanitize(self, text: str) -> str:
        """消毒输入文本——剥离控制字符。

        控制字符（0x00-0x08, 0x0B, 0x0C, 0x0E-0x1F）可能被用于：
          - 混淆注入检测
          - 触发终端/日志解析器异常
        """
        text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
        return text

    def validate(self, text: str) -> InputVerdict:
        """输入验证主入口——按责任链顺序执行所有检查。

        流程：
          1. 配置开关检查 → 如果 enabled=False，直接通过
          2. sanitize()   → 预处理输入
          3. check_length → 长度检查
          4. check_injection → 注入检测
          5. check_sensitive_topics → 敏感话题过滤

        任一检查失败则立即返回（短路执行），不再进行后续检查。
        """
        if not self.config.enabled:
            return InputVerdict(approved=True)

        text = self.sanitize(text)

        for check in [self.check_length, self.check_injection, self.check_sensitive_topics]:
            result = check(text)
            if not result.approved:
                result.sanitized_input = text
                return result

        return InputVerdict(approved=True, sanitized_input=text)
