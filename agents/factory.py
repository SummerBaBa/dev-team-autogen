from autogen_agentchat.agents import AssistantAgent
from autogen_ext.models.openai import OpenAIChatCompletionClient
from autogen_core.models import ModelInfo

from config import AppConfig


def make_client(config: AppConfig, model: str, temp: float):
    return OpenAIChatCompletionClient(
        model=model,
        base_url=config.model.api_base,
        api_key=config.model.api_key,
        temperature=temp,
        max_tokens=config.model.max_tokens,
        model_info=ModelInfo(
            vision=False, function_calling=False,
            json_output=True, family="unknown", structured_output=False,
        ),
    )


def create_agent(
    config: AppConfig,
    name: str,
    model: str,
    temperature: float,
    system_message: str,
    description: str,
) -> AssistantAgent:
    return AssistantAgent(
        name=name,
        model_client=make_client(config, model, temperature),
        system_message=system_message,
        description=description,
    )
