import pytest

from annotation_pipeline_skill.llm.client import LLMGenerateRequest
from annotation_pipeline_skill.llm.openai_compatible import OpenAICompatibleClient
from annotation_pipeline_skill.llm.profiles import LLMProfile


class FakeChatCompletions:
    def __init__(self):
        self.kwargs = None

    async def create(self, **kwargs):
        self.kwargs = kwargs
        return {
            "id": "chatcmpl-1",
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "annotated result",
                    }
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 3},
        }


class FakeClient:
    def __init__(self):
        self.chat_completions = FakeChatCompletions()
        self.chat = type("Chat", (), {"completions": self.chat_completions})()
        self.closed = False

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_openai_compatible_generate_builds_chat_completion_request():
    fake_client = FakeClient()
    profile = LLMProfile(
        name="deepseek_default",
        provider="openai_compatible",
        provider_flavor="deepseek",
        model="deepseek-chat",
        api_key_env="DEEPSEEK_API_KEY",
        base_url="https://api.deepseek.com",
    )
    client = OpenAICompatibleClient(profile, client=fake_client)

    result = await client.generate(
        LLMGenerateRequest(
            instructions="Return JSON only.",
            prompt="Annotate task-1",
            max_output_tokens=512,
        )
    )

    assert fake_client.chat_completions.kwargs == {
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": "Return JSON only."},
            {"role": "user", "content": "Annotate task-1"},
        ],
        "max_tokens": 512,
    }
    assert result.runtime == "openai_compatible"
    assert result.provider == "deepseek_default"
    assert result.model == "deepseek-chat"
    assert result.continuity_handle == "chatcmpl-1"
    assert result.final_text == "annotated result"
    assert result.usage == {"prompt_tokens": 10, "completion_tokens": 3}
    assert result.diagnostics == {"provider_flavor": "deepseek"}


@pytest.mark.asyncio
async def test_openai_compatible_client_closes_underlying_async_client():
    fake_client = FakeClient()
    profile = LLMProfile(
        name="deepseek_default",
        provider="openai_compatible",
        provider_flavor="deepseek",
        model="deepseek-chat",
        api_key_env="DEEPSEEK_API_KEY",
        base_url="https://api.deepseek.com",
    )
    client = OpenAICompatibleClient(profile, client=fake_client)

    await client.aclose()

    assert fake_client.closed is True
