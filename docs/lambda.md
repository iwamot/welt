# Running Welt on AWS Lambda

Instead of the resident container, Welt also runs on AWS Lambda: `lambda_function.py` serves the same conversation flow on the Lambda Python runtime. `template.yaml` is an AWS SAM template that creates the function, its IAM role, and the Function URL.

The setup below assumes your agent is already deployed on AgentCore Runtime and your Slack app is created.

## Setup

1. Clone this repository, build the function package, and deploy:

   ```sh
   git clone https://github.com/iwamot/welt.git
   cd welt
   sam build
   sam deploy --guided
   ```

   During `sam deploy --guided`:

   - The stack parameters are `SlackBotToken`, `SlackSigningSecret` (**Basic Information > Signing Secret**), and `AgentArn`.
   - Answer `y` to `WeltFunction Function Url has no authentication` — Slack requests are verified with the signing secret instead.
   - Note the `FunctionUrl` stack output; the next step needs it.

2. In the Slack app manifest:

   - Set `socket_mode_enabled: false`.
   - Add the URL as `settings.event_subscriptions.request_url`.

## Notes

- Agent replies are bounded by Lambda's 15-minute cap.
- `sam build && sam deploy` redeploys after a change.
- `sam delete` removes everything the setup created.
