from autogen_agentchat.agents import AssistantAgent

from .factory import create_agent
from config import AppConfig
from .registry import AgentRegistry, AgentSpec


def pm_agent(config: AppConfig | None = None) -> AssistantAgent:
    cfg = config or AppConfig.from_env()
    return create_agent(
        config=cfg,
        name="PM",
        model=cfg.model.pm_model,
        temperature=cfg.model.pm_temperature,
        system_message=(
            "你是一名需求分析师。输出 PRD：概述、功能列表、验收标准、技术约束。Markdown 中文。"
        ),
        description="需求分析师",
    )


AgentRegistry.register(AgentSpec(
    name="PM",
    factory=pm_agent,
    description="需求分析师，负责撰写 PRD",
    role="product_manager",
))
