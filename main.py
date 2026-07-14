"""Entry point for Welt, the Slack frontend for AgentCore agents.

Starts a Bolt AsyncApp on Socket Mode and routes new posts to the agent.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal

from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
from slack_bolt.async_app import AsyncAck, AsyncApp
from slack_bolt.context.async_context import AsyncBoltContext
from slack_sdk.http_retry.builtin_async_handlers import AsyncRateLimitErrorRetryHandler
from slack_sdk.web.async_client import AsyncWebClient

from app import bolt_listeners
from app.agent_service import check_local_agent, init_client
from app.bolt_logic import INTERRUPT_ACTION_PATTERN
from app.bolt_middlewares import before_authorize
from app.env import Env, load_env, require_env

logger = logging.getLogger(__name__)


async def main() -> None:
    """
    Start Welt: connect to Slack over Socket Mode and serve until a signal.

    Returns:
        None
    """
    env = load_env(os.environ)
    slack_app_token = require_env(os.environ, "SLACK_APP_TOKEN")
    # Timestamps matter here: unlike Lambda, where CloudWatch stamps every
    # line, a local terminal shows only what the format carries.
    logging.basicConfig(
        level=env.log_level,
        format="%(asctime)s %(levelname)s:%(name)s:%(message)s",
    )
    for warning in env.boot_warnings:
        logger.warning(warning)
    # No region means no ARN: local mode (see Env.agent_region).
    if env.agent_region is None:
        check_local_agent()
    else:
        init_client(region_name=env.agent_region)

    app = create_bolt_app(env)
    handler = AsyncSocketModeHandler(app, slack_app_token)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(signum, stop.set)

    await handler.connect_async()
    await stop.wait()
    logger.info("Shutting down...")
    await handler.close_async()


def create_bolt_app(env: Env) -> AsyncApp:
    """
    Create and configure the Slack Bolt app instance.

    Args:
        env (Env): The validated configuration.

    Returns:
        AsyncApp: The configured Slack Bolt app instance.
    """
    app = AsyncApp(
        token=env.slack_bot_token,
        before_authorize=before_authorize,
        process_before_response=True,
    )
    app.client.retry_handlers.append(AsyncRateLimitErrorRetryHandler(max_retry_count=2))

    async def respond_to_new_post(
        context: AsyncBoltContext, payload: dict, client: AsyncWebClient
    ) -> None:
        await bolt_listeners.respond_to_new_post(
            env=env, context=context, payload=payload, client=client
        )

    async def respond_to_interrupt_action(
        context: AsyncBoltContext, body: dict, payload: dict, client: AsyncWebClient
    ) -> None:
        await bolt_listeners.respond_to_interrupt_action(
            env=env, context=context, body=body, payload=payload, client=client
        )

    app.event("message")(ack=just_ack, lazy=[respond_to_new_post])
    app.action(INTERRUPT_ACTION_PATTERN)(
        ack=just_ack, lazy=[respond_to_interrupt_action]
    )
    return app


async def just_ack(ack: AsyncAck) -> None:
    """
    Acknowledge the incoming request immediately.

    The real work happens in the lazy listener, keeping the 3-second ack.

    Args:
        ack (AsyncAck): The acknowledgment function provided by Slack Bolt.

    Returns:
        None
    """
    await ack()


if __name__ == "__main__":
    asyncio.run(main())
