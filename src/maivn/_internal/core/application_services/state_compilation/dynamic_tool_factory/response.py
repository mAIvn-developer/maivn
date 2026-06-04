"""Response extraction helpers for dynamic dependency tools."""

# pyright: strict
from __future__ import annotations

from collections.abc import Mapping
from typing import cast

# MARK: - Mixin


class DynamicToolFactoryResponseMixin:
    def extract_agent_response(
        self,
        response: object,
        agent_id: str,
        include_response: bool = False,
    ) -> object:
        """Extract the meaningful result from an agent invocation response.

        Design principle: The structured `result` from tool execution is the
        authoritative output. Raw LLM `response` text is unreliable as it may
        contain internal reasoning, concatenated messages, or tool call syntax.
        """
        try:
            result_value = _optional_attr(response, "result")
            if result_value is not None:
                result_value, embedded_response = self._split_session_response(result_value)
                payload: dict[str, object] = {"result": self._unwrap_agent_result(result_value)}
                self._attach_usage_payload(payload, response)
                if include_response:
                    response_value = self._extract_latest_response_entry(
                        _optional_attr(response, "responses")
                    )
                    if not response_value and embedded_response:
                        response_value = embedded_response
                    if not response_value:
                        response_value = self._extract_response_text(response, result_value)
                    if isinstance(response_value, str) and response_value.strip():
                        payload["response"] = response_value.strip()
                return payload

            if _optional_attr(response, "responses") is not None:
                response_value = self._extract_latest_response_entry(
                    _optional_attr(response, "responses")
                )
                if response_value is not None:
                    payload = {"result": None}
                    if include_response:
                        payload["response"] = str(response_value)
                    self._attach_usage_payload(payload, response)
                    return payload

            messages = _optional_attr(response, "messages")
            if isinstance(messages, list) and messages:
                last_message = cast(list[object], messages)[-1]
                content = _optional_attr(last_message, "content")
                if content is not None:
                    payload = {"result": None}
                    if include_response:
                        payload["response"] = str(content)
                    return payload

            return None

        except Exception as e:  # noqa: BLE001 - normalize arbitrary response objects
            raise ValueError(f"Failed to extract result from agent '{agent_id}': {e}") from e

    def _split_session_response(self, value: object) -> tuple[object, str | None]:
        """Split nested SessionResponse-like payloads into result and response."""
        payload = value
        model_dump = _optional_attr(value, "model_dump")
        if callable(model_dump):
            try:
                payload = model_dump(mode="json")
            except Exception:  # noqa: BLE001 - non-Pydantic model_dump variants are ignored
                payload = value

        if isinstance(payload, dict) and "result" in payload:
            embedded_response = None
            payload_map = cast(Mapping[str, object], payload)
            response_value = payload_map.get("response")
            if isinstance(response_value, str):
                embedded_response = response_value
            elif "responses" in payload_map:
                embedded_response = self._extract_latest_response_entry(
                    payload_map.get("responses")
                )
            return payload_map.get("result"), embedded_response

        return value, None

    @staticmethod
    def _extract_latest_response_entry(value: object) -> str | None:
        """Return the last non-empty string from a responses list, or None."""
        if not isinstance(value, list):
            return None
        for item in reversed(cast(list[object], value)):
            if isinstance(item, str) and item.strip():
                return item.strip()
        return None

    def _extract_response_text(self, response: object, result_value: object) -> str | None:
        """Best-effort extraction of response text when response.responses is missing."""
        responses = _optional_attr(response, "responses")
        latest_response = self._extract_latest_response_entry(responses)
        if latest_response:
            return latest_response

        messages = _optional_attr(response, "messages")
        if isinstance(messages, list) and messages:
            last_message = cast(list[object], messages)[-1]
            content = _optional_attr(last_message, "content")
            if isinstance(content, str) and content.strip():
                return content.strip()

        if isinstance(result_value, dict):
            result_map = cast(Mapping[str, object], result_value)
            response_text = result_map.get("response_text")
            if isinstance(response_text, str) and response_text.strip():
                return response_text.strip()
            responses_value = self._extract_latest_response_entry(result_map.get("responses"))
            if responses_value:
                return responses_value
            response_value = result_map.get("response")
            if isinstance(response_value, str) and response_value.strip():
                return response_value.strip()
            primary = result_map.get("primary_response")
            if isinstance(primary, dict):
                primary_text = cast(Mapping[str, object], primary).get("response_text")
                if isinstance(primary_text, str) and primary_text.strip():
                    return primary_text.strip()

        return None

    def _unwrap_agent_result(self, result: object) -> object:
        """Recursively unwrap ArgValue wrappers from agent execution results."""
        if isinstance(result, dict):
            result_map = cast(Mapping[object, object], result)
            if "value" in result_map and len(result_map) == 1:
                return self._unwrap_agent_result(result_map["value"])
            return {key: self._unwrap_agent_result(value) for key, value in result_map.items()}

        if isinstance(result, list):
            return [self._unwrap_agent_result(item) for item in cast(list[object], result)]

        return result

    def unwrap_agent_result(self, result: object) -> object:
        """Recursively unwrap ArgValue wrappers from agent execution results."""
        return self._unwrap_agent_result(result)

    def _attach_usage_payload(self, payload: dict[str, object], response: object) -> None:
        assistant_id = _optional_attr(response, "assistant_id")
        if isinstance(assistant_id, str) and assistant_id.strip():
            payload["assistant_id"] = assistant_id.strip()

        token_usage = _optional_attr(response, "token_usage")
        if token_usage is not None:
            token_usage_payload = _model_dump_json(token_usage)
            if token_usage_payload is not None:
                payload["token_usage"] = token_usage_payload
            else:
                payload["token_usage"] = token_usage

        detailed_usage = _optional_attr(response, "detailed_token_usage")
        if detailed_usage is None:
            metadata = _optional_attr(response, "metadata")
            if isinstance(metadata, dict):
                detailed_usage = cast(Mapping[str, object], metadata).get("detailed_token_usage")
        if detailed_usage is not None:
            detailed_usage_payload = _model_dump_json(detailed_usage)
            if detailed_usage_payload is not None:
                payload["detailed_token_usage"] = detailed_usage_payload
            else:
                payload["detailed_token_usage"] = detailed_usage


# MARK: - Helpers


def _model_dump_json(value: object) -> object | None:
    model_dump = _optional_attr(value, "model_dump")
    if not callable(model_dump):
        return None
    return model_dump(mode="json")


def _optional_attr(value: object, attr: str) -> object | None:
    return cast(object | None, getattr(value, attr, None))
