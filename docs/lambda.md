# Running Welt on AWS Lambda

Instead of the resident container, Welt also runs on AWS Lambda: `lambda_function.py` serves the same conversation flow on the Lambda Python runtime.

## Setup

1. Package the function:

   ```sh
   uv export --frozen --no-dev --no-emit-project > requirements-lambda.txt
   pip install -r requirements-lambda.txt -t package/
   cp -r app lambda_function.py package/
   (cd package && zip -r ../welt-lambda.zip .)
   ```

2. Create a function with the latest Python runtime, handler `lambda_function.lambda_handler`, and a timeout long enough for your agent's replies (execution is bounded by Lambda's 15-minute cap).
3. Set the environment variables: `SLACK_BOT_TOKEN`, `SLACK_SIGNING_SECRET` (**Basic Information > Signing Secret**), and `AGENT_ARN`.
4. Give the function's role permission to invoke your agent, plus `lambda:InvokeFunction` on the function itself (it re-invokes itself to reply after acking each event).
5. Create a Function URL (auth type `NONE`).
6. In the Slack app manifest, set `socket_mode_enabled: false` and add the URL as `settings.event_subscriptions.request_url`.
