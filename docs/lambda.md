# Running Welt on AWS Lambda

On AWS Lambda, Welt serves Slack over HTTP: `lambda_function.py` is the handler, running on the Lambda Python runtime, and Slack sends events and button presses to its Function URL. `template.yaml` is an AWS SAM template that creates the function, its IAM role, and the Function URL.

The setup below assumes you already have an agent on AgentCore Runtime, or a [managed harness](harness.md), for the function to invoke.

## Setup

1. Create a Slack app from [`manifest.yml`](../manifest.yml) — [the pre-filled creation screen][create-app] is the quickest way there. Two values come out of it; serving over HTTP needs no app-level token:

   - In **Basic Information**, copy the **Signing Secret**.
   - In **Install App**, install the app to your workspace and copy the **Bot User OAuth Token** (`xoxb-...`).

2. Clone this repository, build the function package, and deploy:

   ```sh
   git clone https://github.com/iwamot/welt.git
   cd welt
   sam build
   sam deploy --guided
   ```

   During `sam deploy --guided`:

   - The stack parameters are `SlackBotToken` and `SlackSigningSecret` from step 1, and `AgentArn`.
   - Answer `y` to `Allow SAM CLI IAM role creation` — the template creates the function's role.
   - Answer `y` to `WeltFunction Function Url has no authentication. Is this okay?` — it defaults to no, and Slack requests are verified with the signing secret instead.
   - Note the `FunctionUrl` stack output; the next step needs it.

3. In the Slack app manifest, edit the `settings:` section: add a `request_url` with the `FunctionUrl` output under `event_subscriptions` and again under `interactivity`, so events and button presses arrive at the function, then set `socket_mode_enabled` to `false`:

   ```yaml
   settings:
     event_subscriptions:
       request_url: https://<url-id>.lambda-url.<region>.on.aws/  # the FunctionUrl output
       bot_events:
         - message.channels
         - message.groups
         - message.im
         - message.mpim
     interactivity:
       is_enabled: true
       request_url: https://<url-id>.lambda-url.<region>.on.aws/  # the same FunctionUrl
     socket_mode_enabled: false
   ```

   After saving, a warning above the manifest reports that the request URL isn't verified — click **Click here to verify**. The function answers Slack's challenge, which is why it is deployed before this step.

## Notes

- Agent replies are bounded by Lambda's 15-minute cap.
- The optional variables in [Configuration](../README.md#configuration) go under `Environment.Variables` in `template.yaml`.
- `sam build && sam deploy` redeploys after a change.
- `sam delete` removes everything the setup created.

[create-app]: https://api.slack.com/apps?new_app=1&manifest_yaml=display_information%3A%0A%20%20name%3A%20Welt%0Afeatures%3A%0A%20%20app_home%3A%0A%20%20%20%20home_tab_enabled%3A%20false%0A%20%20%20%20messages_tab_enabled%3A%20true%0A%20%20%20%20messages_tab_read_only_enabled%3A%20false%0A%20%20bot_user%3A%0A%20%20%20%20display_name%3A%20Welt%0A%20%20%20%20always_online%3A%20true%0Aoauth_config%3A%0A%20%20scopes%3A%0A%20%20%20%20bot%3A%0A%20%20%20%20%20%20-%20channels%3Ahistory%0A%20%20%20%20%20%20-%20chat%3Awrite%0A%20%20%20%20%20%20-%20files%3Aread%0A%20%20%20%20%20%20-%20files%3Awrite%0A%20%20%20%20%20%20-%20groups%3Ahistory%0A%20%20%20%20%20%20-%20im%3Ahistory%0A%20%20%20%20%20%20-%20mpim%3Ahistory%0A%20%20%20%20%20%20-%20reactions%3Awrite%0A%20%20%20%20%20%20-%20users%3Aread%0Asettings%3A%0A%20%20event_subscriptions%3A%0A%20%20%20%20bot_events%3A%0A%20%20%20%20%20%20-%20message.channels%0A%20%20%20%20%20%20-%20message.groups%0A%20%20%20%20%20%20-%20message.im%0A%20%20%20%20%20%20-%20message.mpim%0A%20%20interactivity%3A%0A%20%20%20%20is_enabled%3A%20true%0A%20%20socket_mode_enabled%3A%20true%0A
