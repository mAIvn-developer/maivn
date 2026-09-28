"""Public token-count model used by SDK reporting adapters.

Cost and provider accounting are intentionally outside the public SDK contract.
"""

from __future__ import annotations

from pydantic import BaseModel

# MARK: Client Model


class TokenUsage(BaseModel):
    """Token usage metrics - public SDK model.

    This model contains only token counts for SDK consumers. Cost information
    is intentionally excluded from the public API.

    Attributes:
        total_tokens: Total number of tokens used.
        input_tokens: Number of input tokens used.
        output_tokens: Number of output tokens used.
        cache_read_tokens: Number of tokens read from cache.
        cache_creation_tokens: Number of tokens written to cache.
        reasoning_tokens: Number of reasoning/thinking tokens used.

    """

    total_tokens: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    reasoning_tokens: int = 0
