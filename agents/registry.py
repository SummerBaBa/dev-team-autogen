from dataclasses import dataclass, field
from typing import Callable

from autogen_agentchat.agents import AssistantAgent


@dataclass
class AgentSpec:
    name: str
    factory: Callable[[], AssistantAgent]
    description: str
    role: str


class AgentRegistry:
    _agents: dict[str, AgentSpec] = {}

    @classmethod
    def register(cls, spec: AgentSpec):
        cls._agents[spec.name] = spec

    @classmethod
    def get(cls, name: str) -> AgentSpec:
        return cls._agents[name]

    @classmethod
    def list_agents(cls) -> list[AgentSpec]:
        return list(cls._agents.values())

    @classmethod
    def roles(cls) -> list[str]:
        return list(cls._agents.keys())
