"""V1-compatible billing read models."""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field


class BillingPlan(BaseModel):
    """The organization's current plan summary."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='ignore')

    name: str
    display_name: str
    tier: str


class BillingCurrentUsage(BaseModel):
    """Current-period usage counters and limits."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='ignore')

    tokens_used: int = 0
    tokens_used_raw: int = 0
    tokens_limit: int = 0
    requests_used: int = 0
    requests_limit: int = 0
    storage_used: int = 0
    storage_limit: int = 0


class BillingUsageApiKey(BaseModel):
    """Usage attributed to one API key."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='ignore')

    api_key_id: str | None = None
    api_key_name: str
    billed_tokens: int = 0
    raw_tokens: int = 0
    requests: int = 0


class BillingUsageProject(BaseModel):
    """Usage attributed to one project, split by API key."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='ignore')

    project_id: str | None = None
    project_name: str
    billed_tokens: int = 0
    raw_tokens: int = 0
    requests: int = 0
    api_keys: list[BillingUsageApiKey] = Field(default_factory=list)


class BillingUsageTotals(BaseModel):
    """Period totals across projects and API keys."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='ignore')

    billed_tokens: int = 0
    raw_tokens: int = 0
    requests: int = 0


class BillingUsageBreakdown(BaseModel):
    """The full org -> project -> API-key usage breakdown for a period."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='ignore')

    organization_id: str
    period_start: str
    period_end: str
    totals: BillingUsageTotals = Field(default_factory=BillingUsageTotals)
    projects: list[BillingUsageProject] = Field(default_factory=list)


class BillingUsage(BaseModel):
    """An organization's billing/usage snapshot for a billing period."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='ignore')

    organization_id: str
    period_start: str
    period_end: str
    plan: BillingPlan | None = None
    usage: BillingCurrentUsage = Field(default_factory=BillingCurrentUsage)
    breakdown: BillingUsageBreakdown


__all__ = [
    'BillingCurrentUsage',
    'BillingPlan',
    'BillingUsage',
    'BillingUsageApiKey',
    'BillingUsageBreakdown',
    'BillingUsageProject',
    'BillingUsageTotals',
]
