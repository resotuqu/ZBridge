from __future__ import annotations

import argparse
import asyncio
import secrets
from datetime import datetime, timedelta
from time import monotonic

import httpx

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.core.security import (
    mask_phone_number,
    normalize_phone_number,
)
from app.services.plusofon import (
    PlusofonClient,
    PlusofonError,
    SMSMessage,
)
from scripts.smoke_plusofon import (
    build_plusofon_client,
    build_ssl_context,
)


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Verify a real inbound SMS -> deployed app "
            "-> outbound SMS round trip through Plusofon."
        )
    )

    parser.add_argument(
        "--phone",
        required=True,
        help="Physical phone from ALLOWED_PHONE_NUMBERS.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=180,
        help="How long to wait for the round trip.",
    )
    parser.add_argument(
        "--poll-seconds",
        type=float,
        default=5.0,
        help="Plusofon dialog polling interval.",
    )

    return parser.parse_args()


def build_marker() -> str:
    token = secrets.token_hex(3).upper()
    return f"ZB-E2E-{token}. Ответь только OK."


def find_round_trip(
    messages: list[SMSMessage],
    *,
    marker: str,
    not_before: datetime,
) -> tuple[SMSMessage, SMSMessage] | None:
    for index, message in enumerate(messages):
        if (
            not message.incoming
            or message.text.strip() != marker
            or message.created_at < not_before
        ):
            continue

        reply = next(
            (
                candidate
                for candidate in messages[index + 1 :]
                if not candidate.incoming
            ),
            None,
        )

        if reply is not None:
            return message, reply

    return None


async def wait_for_round_trip(
    client: PlusofonClient,
    *,
    phone: str,
    marker: str,
    not_before: datetime,
    timeout_seconds: int,
    poll_seconds: float,
) -> tuple[SMSMessage, SMSMessage] | None:
    deadline = monotonic() + timeout_seconds

    while monotonic() < deadline:
        messages = await client.get_dialog(phone, 100)
        result = find_round_trip(
            messages,
            marker=marker,
            not_before=not_before,
        )

        if result is not None:
            return result

        await asyncio.sleep(poll_seconds)

    return None


async def run_smoke_test(
    phone_argument: str,
    *,
    timeout_seconds: int,
    poll_seconds: float,
) -> int:
    settings = get_settings()
    configure_logging(settings.log_level)

    if timeout_seconds <= 0 or poll_seconds <= 0:
        print("E2E smoke test: INVALID TIMEOUT")
        return 2

    try:
        phone = normalize_phone_number(phone_argument)
    except ValueError:
        print("E2E smoke test: INVALID PHONE")
        return 2

    if phone not in settings.allowed_phone_numbers:
        print("E2E smoke test: PHONE NOT ALLOWED")
        return 3

    marker = build_marker()
    not_before = (
        datetime.now(settings.timezone_info)
        - timedelta(seconds=10)
    )

    print("E2E smoke test: WAITING")
    print(f"Phone: {mask_phone_number(phone)}")
    print(
        "Send this exact SMS to the Plusofon number "
        "from the phone above:"
    )
    print(marker)

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
            http_client,
            settings,
        )

        try:
            result = await wait_for_round_trip(
                client,
                phone=phone,
                marker=marker,
                not_before=not_before,
                timeout_seconds=timeout_seconds,
                poll_seconds=poll_seconds,
            )
        except PlusofonError:
            print("E2E smoke test: PLUSOFON ERROR")
            return 4

    if result is None:
        print("E2E smoke test: TIMEOUT")
        return 5

    incoming, outgoing = result

    print("E2E smoke test: ROUND TRIP OK")
    print(f"Incoming: {incoming.created_at.isoformat()}")
    print(f"Outgoing: {outgoing.created_at.isoformat()}")
    print(
        "Confirm that the reply was also received "
        "on the physical phone."
    )

    return 0


if __name__ == "__main__":
    arguments = parse_arguments()

    raise SystemExit(
        asyncio.run(
            run_smoke_test(
                arguments.phone,
                timeout_seconds=(
                    arguments.timeout_seconds
                ),
                poll_seconds=arguments.poll_seconds,
            )
        )
    )
