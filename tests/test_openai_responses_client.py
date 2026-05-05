from pydantic import BaseModel
import pytest

from annotation_pipeline_skill.llm.client import LLMGenerateRequest, LLMStructuredRequest
from annotation_pipeline_skill.llm.openai_responses import OpenAIResponsesClient
from annotation_pipeline_skill.llm.profiles import LLMProfile


class LabelPayload(BaseModel):
    label: str


@pytest.mark.asyncio
async def test_openai_responses_generate_forwards_previous_response_id():
    captured = {}

    class FakeResponses:
        async def create(self, **kwargs):
            captured.update(kwargs)
            return {
                "id": "resp-1",
                "output_text": "done",
                "usage": {"input_tokens": 1, "output_tokens": 2},
                "output": [{"type": "message", "content": [{"type": "output_text", "text": "done"}]}],
            }

    class FakeClient:
        responses = FakeResponses()

    profile = LLMProfile(
        name="openai",
        provider="openai_responses",
        model="gpt-5.4-mini",
        api_key="key",
        base_url="https://api.example/v1",
    )
    client = OpenAIResponsesClient(profile, client=FakeClient())
    result = await client.generate(
        LLMGenerateRequest(
            instructions="annotate carefully",
            input_items=[{"role": "user", "content": "hello"}],
            reasoning={"effort": "medium"},
            continuity_handle="prev-1",
            max_output_tokens=100,
        )
    )

    assert captured["model"] == "gpt-5.4-mini"
    assert captured["instructions"] == "annotate carefully"
    assert captured["input"] == [{"role": "user", "content": "hello"}]
    assert captured["previous_response_id"] == "prev-1"
    assert captured["reasoning"] == {"effort": "medium"}
    assert result.final_text == "done"
    assert result.continuity_handle == "resp-1"


@pytest.mark.asyncio
async def test_openai_responses_parse_structured_uses_sdk_parse():
    captured = {}

    class ParsedText:
        parsed = LabelPayload(label="positive")

    class ParsedMessage:
        type = "message"
        content = [ParsedText()]

    class FakeParsedResponse:
        id = "resp-structured"
        output = [ParsedMessage()]

        def model_dump(self, **kwargs):
            return {"id": self.id, "output": []}

    class FakeResponses:
        async def parse(self, **kwargs):
            captured.update(kwargs)
            return FakeParsedResponse()

    class FakeClient:
        responses = FakeResponses()

    profile = LLMProfile(
        name="openai",
        provider="openai_responses",
        model="gpt-5.4-mini",
        api_key="key",
        base_url="https://api.example/v1",
    )
    client = OpenAIResponsesClient(profile, client=FakeClient())
    result = await client.parse_structured(
        LLMStructuredRequest(
            messages=[{"role": "user", "content": "label this"}],
            text_format=LabelPayload,
            reasoning={"effort": "low"},
        )
    )

    assert captured["text_format"] is LabelPayload
    assert result.output_parsed.label == "positive"
