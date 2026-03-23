from __future__ import annotations

from typing import Any


class DynamicToolFactoryResponseMixin:
    def _extract_agent_response(
        self,
        response: Any,
        agent_id: str,
        include_response: bool = False,
    ) -> Any:
        """Extract the meaningful result from an agent invocation response.

        Design principle: The structured `result` from tool execution is the
        authoritative output. Raw LLM `response` text is unreliable as it may
        contain internal reasoning, concatenated messages, or tool call syntax.
        """
        try:
            if hasattr(response, "result"):
                result_value = getattr(response, "result", None)
                if result_value is not None:
                    result_value, embedded_response = self._split_session_response(result_value)
                    payload: dict[str, Any] = {"result": self._unwrap_agent_result(result_value)}
                    self._attach_usage_payload(payload, response)
                    if include_response:
                        response_value = self._extract_latest_response_entry(
                            getattr(response, "responses", None)
                        )
                        if not response_value and embedded_response:
                            response_value = embedded_response
                        if not response_value:
                            response_value = self._extract_response_text(response, result_value)
                        if isinstance(response_value, str) and response_value.strip():
                            payload["response"] = response_value.strip()
                    return payload

            if hasattr(response, "responses"):
                response_value = self._extract_latest_response_entry(
                    getattr(response, "responses", None)
                )
                if response_value is not None:
                    payload = {"result": None}
                    if include_response:
                        payload["response"] = str(response_value)
                    self._attach_usage_payload(payload, response)
                    return payload

            if hasattr(response, "messages") and response.messages:
                last_message = response.messages[-1]
                if hasattr(last_message, "content"):
                    payload = {"result": None}
                    if include_response:
                        payload["response"] = str(last_message.content)
                    return payload

            return None

        except Exception as e:
            raise ValueError(f"Failed to extract result from agent '{agent_id}': {e}") from e

    def _split_session_response(self, value: Any) -> tuple[Any, str | None]:
        """Split nested SessionResponse-like payloads into result and response."""
        payload = value
        if hasattr(value, "model_dump"):
            try:
                payload = value.model_dump(mode="json")
            except Exception:
                payload = value

        if isinstance(payload, dict) and "result" in payload:
            embedded_response = None
            if "response" in payload:
                embedded_response = payload.get("response")
            elif "responses" in payload:
                embedded_response = self._extract_latest_response_entry(payload.get("responses"))
            return payload.get("result"), embedded_response

        return value, None

    @staticmethod
    def _extract_latest_response_entry(value: Any) -> str | None:
        """Return the last non-empty string from a responses list, or None."""
        if not isinstance(value, list):
            return None
        for item in reversed(value):
            if isinstance(item, str) and item.strip():
                return item.strip()
        return None

    def _extract_response_text(self, response: Any, result_value: Any) -> str | None:
        """Best-effort extraction of response text when response.responses is missing."""
        responses = getattr(response, "responses", None)
        latest_response = self._extract_latest_response_entry(responses)
        if latest_response:
            return latest_response

        if hasattr(response, "messages") and response.messages:
            last_message = response.messages[-1]
            content = getattr(last_message, "content", None)
            if isinstance(content, str) and content.strip():
                return content.strip()

        if isinstance(result_value, dict):
            response_text = result_value.get("response_text")
            if isinstance(response_text, str) and response_text.strip():
                return response_text.strip()
            responses_value = self._extract_latest_response_entry(result_value.get("responses"))
            if responses_value:
                return responses_value
            response_value = result_value.get("response")
            if isinstance(response_value, str) and response_value.strip():
                return response_value.strip()
            primary = result_value.get("primary_response")
            if isinstance(primary, dict):
                primary_text = primary.get("response_text")
                if isinstance(primary_text, str) and primary_text.strip():
                    return primary_text.strip()

        return None

    def _unwrap_agent_result(self, result: Any) -> Any:
        """Recursively unwrap ArgValue wrappers from agent execution results."""
        if isinstance(result, dict):
            if "value" in result and len(result) == 1:
                return self._unwrap_agent_result(result["value"])
            return {k: self._unwrap_agent_result(v) for k, v in result.items()}

        if isinstance(result, list):
            return [self._unwrap_agent_result(item) for item in result]

        return result

    def _attach_usage_payload(self, payload: dict[str, Any], response: Any) -> None:
        assistant_id = getattr(response, "assistant_id", None)
        if isinstance(assistant_id, str) and assistant_id.strip():
            payload["assistant_id"] = assistant_id.strip()

        token_usage = getattr(response, "token_usage", None)
        if token_usage is not None:
            if hasattr(token_usage, "model_dump"):
                payload["token_usage"] = token_usage.model_dump(mode="json")
            else:
                payload["token_usage"] = token_usage

        detailed_usage = getattr(response, "detailed_token_usage", None)
        if detailed_usage is None:
            metadata = getattr(response, "metadata", None)
            if isinstance(metadata, dict):
                detailed_usage = metadata.get("detailed_token_usage")
        if detailed_usage is not None:
            if hasattr(detailed_usage, "model_dump"):
                payload["detailed_token_usage"] = detailed_usage.model_dump(mode="json")
            else:
                payload["detailed_token_usage"] = detailed_usage
