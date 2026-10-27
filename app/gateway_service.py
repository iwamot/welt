"""Signed HTTP transport for a Gateway target addressed without a Runtime ARN."""

from __future__ import annotations

import json
import time

import botocore.session
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError
from botocore.httpsession import URLLib3Session
from botocore.parsers import ResponseParserFactory
from botocore.response import StreamingBody
from botocore.retries.standard import (
    ExponentialBackoff,
    RetryContext,
    RetryPolicy,
    StandardRetryConditions,
)


class GatewayClient:
    """POST Welt's wire payload to a Gateway, using the AWS credential chain.

    Credential refresh, SigV4, HTTP streaming, error parsing, and the standard
    retry policy come from botocore. Retries stop once the stream is returned,
    so consuming part of a reply can never cause the turn to be sent again.
    """

    def __init__(self, *, url: str, region_name: str) -> None:
        session = botocore.session.get_session()
        credentials = session.get_credentials()
        if credentials is None:
            raise NoCredentialsError
        self._credentials = credentials
        self._url = url.rstrip("/") + "/invocations"
        self._region = region_name
        self._http = URLLib3Session(timeout=60)
        # The Gateway forwards Runtime's HTTP response unchanged. Its error
        # shape and retryable session-conflict error use the Runtime model.
        self._operation = session.get_service_model(
            "bedrock-agentcore"
        ).operation_model("InvokeAgentRuntime")
        self._parser = ResponseParserFactory().create_parser("rest-json")
        self._retry = RetryPolicy(StandardRetryConditions(), ExponentialBackoff())

    def invoke(self, *, payload: dict, session_id: str, user_id: str) -> StreamingBody:
        """Send a turn or interrupt response and return its open SSE body."""
        payload_bytes = json.dumps(payload).encode("utf-8")
        attempt = 0
        while True:
            attempt += 1
            request = AWSRequest(
                method="POST",
                url=self._url,
                data=payload_bytes,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "text/event-stream",
                    "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": session_id,
                    "X-Amzn-Bedrock-AgentCore-Runtime-User-Id": user_id,
                },
            )
            SigV4Auth(
                self._credentials.get_frozen_credentials(),
                "bedrock-agentcore",
                self._region,
            ).add_auth(request)
            request.stream_output = True
            try:
                response = self._http.send(request.prepare())
            except BotoCoreError as error:
                context = RetryContext(attempt, caught_exception=error)
                if not self._retry.should_retry(context):
                    raise
            else:
                if 200 <= response.status_code < 300:
                    return StreamingBody(
                        response.raw, response.headers.get("content-length")
                    )
                try:
                    parsed = self._parser.parse(
                        {
                            "headers": response.headers,
                            "status_code": response.status_code,
                            "body": response.content,
                        },
                        self._operation.output_shape,
                    )
                finally:
                    response.raw.close()
                context = RetryContext(
                    attempt,
                    operation_model=self._operation,
                    parsed_response=parsed,
                    http_response=response,
                )
                if not self._retry.should_retry(context):
                    raise ClientError(parsed, "InvokeGateway")
            time.sleep(self._retry.compute_retry_delay(context))
