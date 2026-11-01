from autogen_agentchat.agents import AssistantAgent

from .factory import create_agent
from config import AppConfig
from .registry import AgentRegistry, AgentSpec


def dev_agent(config: AppConfig | None = None) -> AssistantAgent:
    cfg = config or AppConfig.from_env()
    return create_agent(
        config=cfg,
        name="Developer",
        model=cfg.model.dev_model,
        temperature=cfg.model.dev_temperature,
        system_message=(
            "你是一名 Python 全栈工程师。\n"
            "任务：根据 PRD 编写完整可运行的 Python 代码。\n"
            "规则：\n"
            "1. 只输出一个 ```python 代码块，不要有任何说明文字\n"
            "2. 代码必须是可直接 python 运行的完整脚本\n"
            "3. 使用标准库，不要第三方依赖\n"
            "4. 包含 if __name__ == '__main__' 入口"
        ),
        description="开发者",
    )


def fix_agent(config: AppConfig | None = None) -> AssistantAgent:
    cfg = config or AppConfig.from_env()
    return create_agent(
        config=cfg,
        name="Developer",
        model=cfg.model.dev_model,
        temperature=cfg.model.dev_temperature,
        system_message=(
            "你是一名 Python 全栈工程师。\n"
            "任务：根据 PRD 和测试报告修复代码，输出完整可运行的 Python 代码。\n"
            "规则：\n"
            "1. 只输出一个 ```python 代码块，不要有任何说明文字\n"
            "2. 代码必须是可直接 python 运行的完整脚本\n"
            "3. 使用标准库，不要第三方依赖\n"
            "4. 包含 if __name__ == '__main__' 入口"
        ),
        description="修复者",
    )


AgentRegistry.register(AgentSpec(
    name="Developer",
    factory=dev_agent,
    description="开发者，负责编写代码",
    role="developer",
))

AgentRegistry.register(AgentSpec(
    name="Fixer",
    factory=fix_agent,
    description="修复者，负责修复缺陷",
    role="developer",
))
