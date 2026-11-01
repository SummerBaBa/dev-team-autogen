"""
输出安全护栏（OutputGuard）

职责：在 Agent 返回内容送达用户之前，检查输出是否安全。

防护范围（Pipeline 的最后一道防线）：
  1. 危险代码  — 检测 rm -rf /、exec/eval 注入、SQL 注入等
  2. 密钥泄露  — 检测 AWS Key、OpenAI API Key、密码等硬编码
  3. 输出长度  — 防止模型产生极端长响应导致下游问题

风险等级体系：
  - safe:     安全，正常放行
  - medium:   中风险（如超长输出），记录但可选放行
  - high:     高风险（如危险代码），阻止
  - critical: 极高风险（如密钥泄露），立即阻止并告警

与 InputGuard 的关系：
  InputGuard 是上游防线（防恶意输入进入）
  OutputGuard 是下游防线（防恶意输出泄露）
  两者独立工作，互不依赖，任何一方拦截都会阻止该次 Agent 调用。
"""

import re
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class OutputVerdict:
    """输出安全检查结果。

    approved:   是否通过检查
    reason:     拦截原因
    risk_level: 风险等级（safe/medium/high/critical），影响 FlowGuard 的审计记录
    """
    approved: bool
    reason: str = ""
    risk_level: str = "safe"


class OutputGuard:
    """输出安全护栏——对 Agent 的 LLM 输出执行多层安全检查。

    检查流水线（按顺序执行）：
      1. check_length()        → 输出长度限制
      2. check_harmful_code()  → 危险代码/命令模式检测
      3. check_secret_leakage()→ 密钥/密码泄露检测
    """

    def __init__(self, config):
        self.config = config
        # ── 危险代码模式库 ────────────────────────────────────────
        # 这些模式检测 Agent 输出中是否包含可能危害宿主机的代码片段。
        # 设计取舍：宁可误报（false positive）不可漏报（false negative）。
        #
        # 每类模式都做了精确化处理以减少代码生成场景的误报：
        #   文件删除：rm -rf /, rm -rf ~, format c: 等完整命令
        #   任意代码执行：exec(input(...)), eval(input(...)) 等，排除普通 import
        #   混淆命令：base64/decode/encode 解码恶意命令
        #   危险 SQL：DROP/TRUNCATE TABLE/DATABASE
        self._harmful_patterns = [
            # File deletion commands (tightened: require full command context)
            re.compile(r"(rm\s+-rf\s+(/|\~)|format\s+(/|c:|d:)|del\s+/[fs]\s+)", re.IGNORECASE),
            # Arbitrary code execution (narrow: must look like a direct exec/eval call, not import)
            re.compile(r"(exec\s*\(\s*input|eval\s*\(\s*input|__import__\s*\(\s*['\"])", re.IGNORECASE),
            # Obfuscated command execution
            re.compile(r"(base64|decode|encode)\s*\(\s*['\"].*(cmd|powershell|bash)", re.IGNORECASE),
            # Dangerous SQL operations
            re.compile(r"(drop|truncate)\s+(table|database)\s+", re.IGNORECASE),
        ]
        # ── 密钥泄露检测模式库 ────────────────────────────────────
        # AWS Access Key 格式：AKIA 开头 + 16 位大写字母/数字
        # OpenAI API Key 格式：sk- 开头 + 20+ 位字母/数字
        # 通用密钥/密码赋值：api_key = "xxx", password = "xxx" 等
        self._secret_patterns = [
            re.compile(r"(AKIA[0-9A-Z]{16})"),
            re.compile(r"(sk-[a-zA-Z0-9]{20,})"),
            re.compile(r"(api[_-]?key|apikey|secret)\s*[=:]\s*['\"][^'\"]+['\"]", re.IGNORECASE),
            re.compile(r"(password|passwd|pwd)\s*[=:]\s*['\"][^'\"]+['\"]", re.IGNORECASE),
        ]

    def check_harmful_code(self, text: str) -> OutputVerdict:
        """检测输出中是否包含危险代码或命令。

        由于 Dev/Fix Agent 设计为生成 Python 代码，输出中天然包含
        大量代码。因此模式设计必须在"阻止恶意代码"和"允许正常开发"
        之间取得平衡。

        策略：
          - 只匹配特定上下文中的危险调用（如 exec(input) 而非普通 exec）
          - 文件删除命令需要完整命令上下文（rm -rf / 而非单纯 rm）
        """
        for pattern in self._harmful_patterns:
            if pattern.search(text):
                return OutputVerdict(
                    approved=False,
                    reason=f"检测到潜在危险代码模式: {pattern.pattern[:50]}...",
                    risk_level="high"
                )
        return OutputVerdict(approved=True)

    def check_secret_leakage(self, text: str) -> OutputVerdict:
        """检测输出是否泄露了密钥、令牌或密码。

        这是 LLM 应用特有的安全风险：
          模型可能"记住"了训练数据中的密钥，或在代码生成中
          生成包含测试密钥的代码。

        使用 findall 返回所有匹配（而非 search 首次匹配），
        以便在拦截时报告泄露的总数量。
        """
        for pattern in self._secret_patterns:
            matches = pattern.findall(text)
            if matches:
                return OutputVerdict(
                    approved=False,
                    reason=f"检测到可能的密钥泄露 ({len(matches)} 处匹配)",
                    risk_level="critical"
                )
        return OutputVerdict(approved=True)

    def check_length(self, text: str) -> OutputVerdict:
        """检查输出长度是否超过配置限制。

        模型有时会产生循环重复输出（Repetition Loop），
        长度检查可以及早阻断这类异常行为。
        """
        if len(text) > self.config.output_max_length:
            return OutputVerdict(
                approved=False,
                reason=f"输出过长: {len(text)} 字符 (限制: {self.config.output_max_length})",
                risk_level="medium"
            )
        return OutputVerdict(approved=True)

    def validate(self, text: str, context: Optional[dict] = None) -> OutputVerdict:
        """输出验证主入口——按责任链顺序执行所有检查。

        context 参数为将来扩展预留（可传入 agent_name、phase 等信息
        以便实现上下文感知的检查策略）。
        """
        if not self.config.enabled:
            return OutputVerdict(approved=True)

        for check in [self.check_length, self.check_harmful_code, self.check_secret_leakage]:
            result = check(text)
            if not result.approved:
                return result

        return OutputVerdict(approved=True)
