from __future__ import annotations

import argparse
import asyncio
import ssl

import httpx

from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.core.security import (
    mask_phone_number,
    normalize_phone_number,
)
from app.services.plusofon import (
    PlusofonAuthError,
    PlusofonClient,
    PlusofonDeliveryUnknownError,
    PlusofonError,
    PlusofonTransientError,
)


TEST_MESSAGE = "ZBridge: тест исходящих SMS."


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Send one guarded Plusofon "
            "smoke-test SMS."
        )
    )

    parser.add_argument(
        "--to",
        required=True,
        help=(
            "Recipient from "
            "ALLOWED_PHONE_NUMBERS."
        ),
    )

    parser.add_argument(
        "--confirm-send",
        action="store_true",
        help="Actually send the paid SMS.",
    )

    return parser.parse_args()


def build_ssl_context(
    ca_bundle: str | None,
) -> ssl.SSLContext:
    context = ssl.create_default_context()

    if ca_bundle:
        context.load_verify_locations(
            cafile=ca_bundle
        )

    return context


def build_plusofon_client(
    http_client: httpx.AsyncClient,
    settings: Settings,
) -> PlusofonClient:
    return PlusofonClient(
        http_client,
        token=(
            settings
            .plusofon_token
            .get_secret_value()
        ),
        client_id=settings.plusofon_client_id,
        number_id=settings.plusofon_number_id,
        api_base_url=settings.plusofon_api_base_url,
        own_number=settings.plusofon_number,
        default_timezone=settings.timezone_info,
    )


async def run_smoke_test(
    recipient_argument: str,
    *,
    confirm_send: bool,
) -> int:
    settings = get_settings()
    configure_logging(settings.log_level)

    try:
        recipient = normalize_phone_number(
            recipient_argument
        )
    except ValueError:
        print(
            "Plusofon smoke test: INVALID PHONE"
        )
        return 2

    if recipient not in settings.allowed_phone_numbers:
        print(
            "Plusofon smoke test: "
            "PHONE NOT ALLOWED"
        )
        return 3

    masked_recipient = mask_phone_number(
        recipient
    )

    if not confirm_send:
        print("Plusofon smoke test: DRY RUN")
        print(f"Recipient: {masked_recipient}")
        print(f"Text: {TEST_MESSAGE}")
        print(
            "SMS was not sent. "
            "Add --confirm-send to send it."
        )
        return 0

    ssl_context = build_ssl_context(
        settings.gigachat_ca_bundle
    )

    timeout = httpx.Timeout(
        settings.http_timeout_seconds
    )

    async with httpx.AsyncClient(
        timeout=timeout,
        verify=ssl_context,
    ) as http_client:
        client = build_plusofon_client(
            http_client,
            settings,
        )

        try:
            result = await client.send(
                recipient,
                TEST_MESSAGE,
            )
        except PlusofonAuthError:
            print(
                "Plusofon smoke test: AUTH ERROR"
            )
            return 4
        except PlusofonDeliveryUnknownError:
            print(
                "Plusofon smoke test: "
                "DELIVERY UNKNOWN"
            )
            print(
                "Do not repeat immediately: "
                "the SMS may be accepted."
            )
            return 5
        except PlusofonTransientError:
            print(
                "Plusofon smoke test: "
                "TEMPORARY ERROR"
            )
            return 6
        except PlusofonError:
            print(
                "Plusofon smoke test: API ERROR"
            )
            return 7

    print("Plusofon smoke test: OK")
    print(f"Recipient: {masked_recipient}")
    print(f"Message ID: {result.message_id}")
    print(f"PDU: {result.pdu_count}")

    return 0


if __name__ == "__main__":
    arguments = parse_arguments()

    raise SystemExit(
        asyncio.run(
            run_smoke_test(
                arguments.to,
                confirm_send=(
                    arguments.confirm_send
                ),
            )
        )
    )
