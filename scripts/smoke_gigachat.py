from __future__ import annotations

import asyncio
import ssl

import httpx

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.schemas.messages import ChatMessage, ChatRole
from app.services.gigachat import (
    GigaChatAuthError,
    GigaChatClient,
    GigaChatError,
    GigaChatTransientError,
)


def build_ssl_context(
    ca_bundle: str | None,
) -> ssl.SSLContext:
    context = ssl.create_default_context()

    if ca_bundle:
        context.load_verify_locations(
            cafile=ca_bundle
        )

    return context


async def run_smoke_test() -> int:
    settings = get_settings()
    configure_logging(settings.log_level)

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
        client = GigaChatClient(
            http_client,
            credentials=(
                settings
                .gigachat_credentials
                .get_secret_value()
            ),
            scope=settings.gigachat_scope,
            api_base_url=(
                settings.gigachat_api_base_url
            ),
            oauth_url=(
                settings.gigachat_oauth_url
            ),
        )

        try:
            answer = await client.chat(
                [
                    ChatMessage(
                        role=ChatRole.SYSTEM,
                        content=(
                            "Ответь только одним числом "
                            "без пояснений."
                        ),
                    ),
                    ChatMessage(
                        role=ChatRole.USER,
                        content=(
                            "Сколько будет два плюс два?"
                        ),
                    ),
                ],
                model=settings.gigachat_model,
            )
        except GigaChatAuthError:
            print(
                "GigaChat smoke test: AUTH ERROR"
            )
            return 2
        except GigaChatTransientError:
            print(
                "GigaChat smoke test: "
                "TEMPORARY ERROR"
            )
            return 3
        except GigaChatError:
            print(
                "GigaChat smoke test: API ERROR"
            )
            return 4

    print("GigaChat smoke test: OK")
    print(f"Model: {settings.gigachat_model}")
    print(f"Answer: {answer}")

    return 0


if __name__ == "__main__":
    raise SystemExit(
        asyncio.run(run_smoke_test())
    )