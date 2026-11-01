"""
配置模块定义

使用 @dataclass 组织配置，形成三层嵌套结构：
  AppConfig          ← 顶层配置
    ├─ ModelConfig   ← 模型相关（API 地址、模型名、温度等）
    ├─ GuardConfig   ← 安全护栏相关（输入/输出检查参数）
    └─ EvalConfig    ← 评估追踪相关（TraceLogger 目录、benchmark 轮次等）

设计原则：
  1. 环境变量优先：所有关键配置项都支持通过 os.getenv() 从环境变量读取
  2. 合理默认值：提供可直接运行的默认配置（本地 127.0.0.1:9000 API）
  3. dataclass 不可变性：配置一旦加载，运行期间不改写
"""

import os
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ModelConfig:
    """大语言模型连接配置。

    每个 Agent（PM/Dev/QA）可以使用不同模型和温度参数，因为：
      - PM 需要 低温度（0.3） → 输出稳定、格式规范的文档
      - Dev 需要 更低温度（0.2）→ 生成确定性高的代码
      - QA 需要 中等温度（0.3）→ 覆盖合理的测试边界

    可在简历中描述为：
      "针对多 Agent 场景设计了差异化模型配置策略，PM/Dev/QA 分别使用
       不同的模型实例和温度参数以优化各环节输出质量。"
    """
    api_base: str = os.getenv("API_BASE", "http://127.0.0.1:9000/v1")
    api_key: str = os.getenv("API_KEY", "12345678")
    pm_model: str = os.getenv("PM_MODEL", "Qwen3.8-27B-MLX-4bit")
    dev_model: str = os.getenv("DEV_MODEL", "Qwen3-Coder-30B-A3B-Instruct-MLX-4bit")
    pm_temperature: float = 0.3
    dev_temperature: float = 0.2
    qa_temperature: float = 0.3
    max_tokens: int = 4096


@dataclass
class GuardConfig:
    """安全护栏（Guardrails）配置，控制输入/输出的安全检查行为。

    这是整个安全体系的可调参数中心：
      - enabled：总开关，可一键关闭护栏（调试/开发模式）
      - max_input_length / output_max_length：防止 token 耗尽攻击
      - sensitive_keywords：用于 InputGuard 的提示注入关键词库
      - blocked_topics：用于 InputGuard 的敏感话题过滤列表

    注：sensitive_keywords 与 input_guard.py 中的 _injection_patterns
    正则数组是互补关系——前者做精确关键词匹配，后者做正则模糊匹配。
    """
    enabled: bool = True
    max_input_length: int = 10000
    sensitive_keywords: list[str] = field(default_factory=lambda: [
        "ignore previous instructions", "ignore all rules",
        "you are now", "system override", "jailbreak",
        "dan mode", "do anything now", "bypass",
        "忽略之前的指令", "忽略所有规则",
        "你现在是", "系统覆盖", "越狱",
    ])
    blocked_topics: list[str] = field(default_factory=lambda: [
        "如何制作武器", "毒品制作", "网络攻击教程", "恶意代码",
        "个人隐私窃取", "诈骗方法", "暴力内容",
    ])
    output_max_length: int = 50000
    require_human_approval: bool = True


@dataclass
class EvalConfig:
    """评估追踪配置。

    tracing_enabled：控制 TraceLogger 是否记录本地 JSON 追踪文件
      此开关独立于 LangSmith（LangSmith 由 import 是否成功控制），
      两者可以同时启用或单独启用。

    trace_dir：本地追踪文件存储目录
    benchmark_rounds：批量基准测试时默认运行轮数
    """
    tracing_enabled: bool = True
    trace_dir: str = "output/traces"
    benchmark_rounds: int = 3


@dataclass
class AppConfig:
    """应用顶层配置，聚合所有子配置。

    使用嵌套 dataclass 模式（Nested Configuration Pattern）：
      - 每个子配置独立定义，职责单一
      - 顶层聚合，方便传递和访问
      - 支持 from_env() 工厂方法从环境变量构造

    from_env() 的设计意图：
      - 主程序入口处调用一次，全局持有单例
      - 所有模块通过参数接收 config 实例，不访问全局变量
      - 方便在测试中注入 Mock 配置
    """
    model: ModelConfig = field(default_factory=ModelConfig)
    guard: GuardConfig = field(default_factory=GuardConfig)
    eval: EvalConfig = field(default_factory=EvalConfig)
    output_dir: str = "output"

    @classmethod
    def from_env(cls):
        return cls()
