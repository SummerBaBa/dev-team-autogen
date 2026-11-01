from autogen_agentchat.agents import AssistantAgent

from .factory import create_agent
from config import AppConfig
from .registry import AgentRegistry, AgentSpec


def qa_agent(config: AppConfig | None = None) -> AssistantAgent:
    cfg = config or AppConfig.from_env()
    return create_agent(
        config=cfg,
        name="QA",
        model=cfg.model.pm_model,
        temperature=cfg.model.qa_temperature,
        system_message=(
            "你是一名测试工程师。测试代码，输出测试报告：用例、边界条件、问题列表。"
        ),
        description="测试员",
    )


AgentRegistry.register(AgentSpec(
    name="QA",
    factory=qa_agent,
    description="测试工程师，负责测试和回归验证",
    role="qa",
))
