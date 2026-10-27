# Routing a Runtime through AgentCore Gateway

Welt can invoke a Runtime through an IAM-authenticated AgentCore Gateway:

```text
Slack ↔ Welt ↔ AgentCore Gateway ↔ Runtime
```

The Gateway's HTTP Runtime target forwards the agent's JSON requests and SSE replies. Conversation turns and interrupt responses use the same connection. The agent keeps using its existing welt-io adapter.

## Create the Gateway

Deploy an agent first, following its adapter's example README. Use its Runtime ARN and region for the following commands. The [CloudFormation template](../examples/gateway/template.yaml) creates an IAM-authenticated Gateway, a target named `agent`, and a role allowed to invoke that Runtime. The target forwards the Runtime session and user ID headers.

```sh
aws cloudformation deploy \
  --region <runtime-region> \
  --stack-name welt-gateway-example \
  --template-file examples/gateway/template.yaml \
  --capabilities CAPABILITY_IAM \
  --parameter-overrides RuntimeArn=<runtime-arn>

aws cloudformation describe-stacks \
  --region <runtime-region> \
  --stack-name welt-gateway-example \
  --query 'Stacks[0].Outputs' \
  --output table
```

The `RuntimeQualifier` parameter selects the Runtime endpoint, defaulting to `DEFAULT`.

## Connect Welt

Set these in Welt's `.env`, using the `AgentGatewayUrl` stack output:

```sh
AGENT_ARN=<runtime-arn>
AGENT_GATEWAY_URL=https://<gateway-id>.gateway.bedrock-agentcore.<runtime-region>.amazonaws.com/agent
```

Use the Gateway target's base URL, ending in `/agent`. The SDK builds the invocation path and signs each request with Welt's AWS credentials. The Gateway must be in the Runtime's region.

Grant Welt's AWS identity `bedrock-agentcore:InvokeGateway` on the `GatewayArn` stack output, then start Welt as described in the [Quick Start](../README.md#quick-start).

For Welt on Lambda, pass `AgentGatewayUrl` and `GatewayArn` from the Gateway stack outputs as parameters when deploying Welt's `template.yaml`, along with its Slack credentials and `AgentArn`.

## Verify the connection

In Slack, ask the agent to call a tool and return a file. For an agent with interrupt support, ask for an action requiring approval, then answer its question and check that the same run resumes. Check both approval and rejection.

## Remove the Gateway

Remove `AGENT_GATEWAY_URL` from Welt's configuration and restart it to invoke the Runtime directly. On Lambda, deploy with both `AgentGatewayUrl` and `GatewayArn` empty. Then delete the Gateway stack:

```sh
aws cloudformation delete-stack \
  --region <runtime-region> \
  --stack-name welt-gateway-example

aws cloudformation wait stack-delete-complete \
  --region <runtime-region> \
  --stack-name welt-gateway-example
```

See [AWS's HTTP Runtime target documentation](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-target-http-runtime.html) for Gateway configuration and routing.
