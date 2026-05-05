from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ValidationError


class StructuredParseError(ValueError):
    pass


def extract_parsed_output(response: Any) -> BaseModel:
    for item in getattr(response, "output", []) or []:
        if getattr(item, "type", None) != "message":
            continue
        for content in getattr(item, "content", []) or []:
            parsed = getattr(content, "parsed", None)
            if isinstance(parsed, BaseModel):
                return parsed
    raise StructuredParseError("structured response did not include parsed output")


def validate_structured_text(text_format: type[BaseModel], payload: str) -> BaseModel:
    try:
        return text_format.model_validate_json(payload)
    except ValidationError as exc:
        raise StructuredParseError(str(exc)) from exc


def build_correction_prompt(schema_name: str, validation_error: str, invalid_payload: str) -> str:
    return (
        f"Correct the following {schema_name} JSON so it satisfies the schema. "
        "Return only valid JSON.\n\n"
        f"Validation error:\n{validation_error}\n\n"
        f"Invalid payload:\n{invalid_payload}"
    )
