# pyright: strict
"""Typed response models for the SDK billing/usage client.

Mirror the maivn-server ``/billing/usage`` response: the organization's plan,
current usage-vs-limits, and the org -> project -> API-key usage breakdown
(including the explicit "Unknown API key" / unattributed buckets). ``extra`` is
ignored so the SDK keeps deserializing if the server adds fields.
"""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field

# MARK: - Plan / usage


class BillingPlan(BaseModel):
    """The organization's current plan summary."""

    name: str
    display_name: str
    tier: str
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore")


class BillingCurrentUsage(BaseModel):
    """Current-period usage counters and their limits (-1 means unlimited)."""

    tokens_used: int = 0
    tokens_used_raw: int = 0
    tokens_limit: int = 0
    requests_used: int = 0
    requests_limit: int = 0
    storage_used: int = 0
    storage_limit: int = 0
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore")


# MARK: - Usage breakdown (org -> project -> API key)


class BillingUsageApiKey(BaseModel):
    """Usage attributed to one API key. ``api_key_id`` is ``None`` for the
    "Unknown API key" bucket (usage that predates key attribution / direct usage)."""

    api_key_id: str | None = None
    api_key_name: str
    billed_tokens: int = 0
    raw_tokens: int = 0
    requests: int = 0
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore")


class BillingUsageProject(BaseModel):
    """Usage attributed to one project, split by API key. ``project_id`` is
    ``None`` for the unattributed bucket."""

    project_id: str | None = None
    project_name: str
    billed_tokens: int = 0
    raw_tokens: int = 0
    requests: int = 0
    api_keys: list[BillingUsageApiKey] = Field(default_factory=list[BillingUsageApiKey])
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore")


class BillingUsageTotals(BaseModel):
    """Period totals across every project/API key."""

    billed_tokens: int = 0
    raw_tokens: int = 0
    requests: int = 0
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore")


class BillingUsageBreakdown(BaseModel):
    """The full org -> project -> API-key usage breakdown for a period."""

    organization_id: str
    period_start: str
    period_end: str
    totals: BillingUsageTotals = Field(default_factory=BillingUsageTotals)
    projects: list[BillingUsageProject] = Field(default_factory=list[BillingUsageProject])
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore")


# MARK: - Top-level response


class BillingUsage(BaseModel):
    """An organization's billing/usage snapshot for a billing period."""

    organization_id: str
    period_start: str
    period_end: str
    plan: BillingPlan | None = None
    usage: BillingCurrentUsage = Field(default_factory=BillingCurrentUsage)
    breakdown: BillingUsageBreakdown
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore")


__all__ = [
    "BillingPlan",
    "BillingCurrentUsage",
    "BillingUsageApiKey",
    "BillingUsageProject",
    "BillingUsageTotals",
    "BillingUsageBreakdown",
    "BillingUsage",
]
