# pyright: strict
"""Billing/usage methods for the maivn SDK Client."""

from __future__ import annotations

from typing import TypeVar

from pydantic import BaseModel

from maivn._internal.api.billing_models import BillingUsage

from .http import JsonValue, QueryValue

# MARK: Types

ModelT = TypeVar("ModelT", bound=BaseModel)

_BILLING_USAGE_PATH = "/billing/usage"


# MARK: ClientBillingMixin


class ClientBillingMixin:
    """Billing/usage read methods mixed into the Client class."""

    # MARK: - HTTP Hooks (provided by ClientHttpMixin via MRO)

    def _get_json(self, _path: str) -> JsonValue:
        raise NotImplementedError

    @staticmethod
    def _with_query(_path: str, _params: dict[str, QueryValue]) -> str:
        raise NotImplementedError

    @staticmethod
    def _validate_model(_model_type: type[ModelT], _payload: object) -> ModelT:
        raise NotImplementedError

    # MARK: - Billing / usage

    def get_usage(self, *, year: int | None = None, month: int | None = None) -> BillingUsage:
        """Return this API key's organization billing/usage for a period.

        Includes the current plan, usage-vs-limits, and the org -> project ->
        API-key usage breakdown (the same breakdown that appears on invoices),
        so integrators can reconcile spend across their own customers.

        Defaults to the current calendar month; pass both ``year`` and ``month``
        for a past period.

        Requires a full-access (``all``) API key — billing is account-level data,
        so read-only / restricted keys are rejected by the server (403).
        """
        path = self._with_query(_BILLING_USAGE_PATH, {"year": year, "month": month})
        payload = self._get_json(path)
        return self._validate_model(BillingUsage, payload)


__all__ = ["ClientBillingMixin"]
