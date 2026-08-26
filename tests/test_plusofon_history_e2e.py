"""
Opt-in, read-only integration check against the real Plusofon API.

Disabled by default (and therefore in CI): only runs when
PLUSOFON_E2E_READONLY=1 is set. Credentials come exclusively from the
environment via the normal Settings machinery -- to use real
credentials rather than the fake ones tests/conftest.py otherwise
provides, export them (PLUSOFON_TOKEN, PLUSOFON_CLIENT_ID,
PLUSOFON_NUMBER_ID, PLUSOFON_NUMBER, PLUSOFON_API_BASE_URL, etc.)
*before* invoking pytest -- conftest.py only fills in values that
aren't already set.

This check only ever calls list_messages() (GET /api/v1/sms). It never
sends an SMS, and it never prints or logs the token, any phone number,
or message text -- only a message count and, on failure, the raised
exception's type name.
"""

import os
from datetime import datetime, timedelta

import httpx
import pytest

from app.core.config import get_settings
from app.services.plusofon import PlusofonError
from scripts.smoke_plusofon import (
    build_plusofon_client,
    build_ssl_context,
)


pytestmark = pytest.mark.skipif(
    os.environ.get("PLUSOFON_E2E_READONLY") != "1",
    reason=(
        "Opt-in only: set PLUSOFON_E2E_READONLY=1 (and real "
        "Plusofon credentials in the environment) to run a real, "
        "read-only GET /api/v1/sms request. Skipped by default "
        "and in CI."
    ),
)


@pytest.mark.asyncio
async def test_real_plusofon_history_request_succeeds_and_is_read_only() -> (
    None
):
    settings = get_settings()
    ssl_context = build_ssl_context(
        settings.gigachat_ca_bundle
    )

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(
            settings.http_timeout_seconds
        ),
        verify=ssl_context,
    ) as http_client:
        client = build_plusofon_client(
            http_client, settings
        )

        now = datetime.now(settings.timezone_info)
        date_from = now - timedelta(days=1)

        try:
            messages = await client.list_messages(
                date_from=date_from,
                date_to=now,
                limit=5,
            )
        except PlusofonError as exc:
            pytest.fail(
                "Real Plusofon history request failed: "
                f"{type(exc).__name__}"
            )

    assert isinstance(messages, list)
    print(
        f"PLUSOFON_E2E_READONLY: fetched {len(messages)} "
        "message(s)."
    )
