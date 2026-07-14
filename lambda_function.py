"""AWS Lambda entry point for Welt, the Slack frontend for AgentCore agents.

Serves the same conversation flow as `main.py` over the Slack Events API
(HTTP request URL) instead of Socket Mode, for deployments on the Lambda
Python runtime. Bolt acks each event within Slack's 3-second window and
re-invokes this same function asynchronously to run the lazy listener, so
the function's IAM role must allow `lambda:InvokeFunction` on the function
itself. Configure the handler as `lambda_function.lambda_handler` (the
Python runtime default) and point the Slack app's Events API request URL at
the function.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import os

from slack_bolt import Ack, App, BoltContext
from slack_bolt.adapter.aws_lambda import SlackRequestHandler
from slack_sdk.http_retry.builtin_async_handlers import AsyncRateLimitErrorRetryHandler
from slack_sdk.http_retry.builtin_handlers import RateLimitErrorRetryHandler
from slack_sdk.web.async_client import AsyncWebClient

from app import bolt_listeners
from app.agent_service import check_local_agent, init_client
from app.bolt_logic import INTERRUPT_ACTION_PATTERN
from app.bolt_middlewares import before_authorize_http
from app.env import Env, load_env, require_env

logger = logging.getLogger(__name__)


def lambda_handler(event: dict, context: object) -> dict:
    """
    Handle one Lambda invocation: an Events API request or a lazy run.

    Args:
        event (dict): The Lambda event (an HTTP request from a Function URL
            or API Gateway, or Bolt's lazy-listener re-invocation).
        context (object): The Lambda context object.

    Returns:
        dict: The HTTP response for the Lambda runtime.
    """
    return _get_handler().handle(event, context)


@functools.cache
def _get_handler() -> SlackRequestHandler:
    """
    Build the request handler once per Lambda execution environment.

    Deferring the build to the first invocation (instead of import time)
    keeps the module importable without configuration; a misconfigured
    function still fails on its cold start with a clear error.

    Returns:
        SlackRequestHandler: The handler wrapping the configured Bolt app.
    """
    env = load_env(os.environ)
    signing_secret = require_env(os.environ, "SLACK_SIGNING_SECRET")
    SlackRequestHandler.clear_all_log_handlers()
    logging.basicConfig(level=env.deps_log_level)
    # LOG_LEVEL applies to Welt's own loggers only; the root level above
    # (DEPS_LOG_LEVEL) covers the dependencies. See Env.deps_log_level.
    logging.getLogger("app").setLevel(env.log_level)
    logger.setLevel(env.log_level)
    for warning in env.boot_warnings:
        logger.warning(warning)
    # No region means no ARN: local mode (see Env.agent_region). On Lambda
    # that can only be a forgotten AGENT_ARN, and the check fails the cold
    # start with a message that names it.
    if env.agent_region is None:
        check_local_agent()
    else:
        init_client(region_name=env.agent_region)
    return SlackRequestHandler(app=create_bolt_app(env, signing_secret))


def create_bolt_app(env: Env, signing_secret: str) -> App:
    """
    Create and configure the sync Slack Bolt app instance for HTTP serving.

    The listener body is the same async flow Socket Mode runs; each lazy
    invocation carries one message, so it bridges with `asyncio.run` and an
    `AsyncWebClient` of its own.

    Args:
        env (Env): The validated configuration.
        signing_secret (str): The Slack signing secret for request
            verification.

    Returns:
        App: The configured Slack Bolt app instance.
    """
    app = App(
        token=env.slack_bot_token,
        signing_secret=signing_secret,
        before_authorize=before_authorize_http,
        process_before_response=True,
    )
    app.client.retry_handlers.append(RateLimitErrorRetryHandler(max_retry_count=2))

    def respond_to_new_post(context: BoltContext, payload: dict) -> None:
        client = AsyncWebClient(token=env.slack_bot_token)
        client.retry_handlers.append(AsyncRateLimitErrorRetryHandler(max_retry_count=2))
        asyncio.run(
            bolt_listeners.respond_to_new_post(
                env=env, context=context, payload=payload, client=client
            )
        )

    def respond_to_interrupt_action(
        context: BoltContext, body: dict, payload: dict
    ) -> None:
        client = AsyncWebClient(token=env.slack_bot_token)
        client.retry_handlers.append(AsyncRateLimitErrorRetryHandler(max_retry_count=2))
        asyncio.run(
            bolt_listeners.respond_to_interrupt_action(
                env=env, context=context, body=body, payload=payload, client=client
            )
        )

    app.event("message")(ack=just_ack, lazy=[respond_to_new_post])
    app.action(INTERRUPT_ACTION_PATTERN)(
        ack=just_ack, lazy=[respond_to_interrupt_action]
    )
    return app


def just_ack(ack: Ack) -> None:
    """
    Acknowledge the incoming request immediately.

    The real work happens in the lazy listener, keeping the 3-second ack.

    Args:
        ack (Ack): The acknowledgment function provided by Slack Bolt.

    Returns:
        None
    """
    ack()
