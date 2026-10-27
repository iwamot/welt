# Routing a Runtime through AgentCore Gateway

Welt can invoke a Runtime through an IAM-authenticated AgentCore Gateway:

```text
Slack ↔ Welt ↔ AgentCore Gateway ↔ Runtime
```

The Gateway's HTTP Runtime target forwards the agent's JSON requests and SSE replies. Conversation turns and interrupt responses use the same route. The agent keeps using its existing welt-io adapter.

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

The `RuntimeQualifier` parameter selects the Runtime endpoint, defaulting to `DEFAULT`. Configure the Runtime ARN and endpoint on this Gateway target when changing its destination.

## Connect Welt

Choose how Welt addresses the Gateway:

| Mode | Environment variables | Use it when |
| --- | --- | --- |
| Gateway URL | `AGENT_GATEWAY_URL` | Welt should know the Gateway entry point, with the Runtime ARN and endpoint managed on the Gateway. |
| Runtime SDK through Gateway | `AGENT_ARN` and `AGENT_GATEWAY_URL` | You want to keep an ARN-based SDK configuration and send its calls through the Gateway. |

Use the `AgentGatewayUrl` stack output as the target base URL, ending in `/agent`. The Gateway must be in the Runtime's region. Both modes use Welt's standard AWS credential chain, preserve the Runtime session and verified Slack user ID, and support streaming replies, files, and interrupts.

Grant Welt's AWS identity `bedrock-agentcore:InvokeGateway` on the `GatewayArn` stack output. The Gateway role invokes the Runtime with its own permissions.

### Gateway URL

Remove `AGENT_ARN` from Welt's `.env` and set:

```sh
AGENT_GATEWAY_URL=https://<gateway-id>.gateway.bedrock-agentcore.<region>.amazonaws.com/agent
```

Welt sends a signed POST to `<AGENT_GATEWAY_URL>/invocations`, using the region from the Gateway URL. The Gateway selects the Runtime and endpoint from its target configuration.

Start Welt as described in the [Quick Start](../README.md#quick-start) or [resident process guide](socket.md).

### Runtime SDK through Gateway

Set both variables in Welt's `.env`:

```sh
AGENT_ARN=<runtime-arn>
AGENT_GATEWAY_URL=https://<gateway-id>.gateway.bedrock-agentcore.<region>.amazonaws.com/agent
```

Welt calls the SDK's `invoke_agent_runtime` with the Gateway as its endpoint URL. The ARN supplies the Runtime identifier, region, and optional endpoint qualifier in the SDK request. The Gateway's target configuration still determines where the call is forwarded.

Start Welt as described in the [Quick Start](../README.md#quick-start) or [resident process guide](socket.md).

### Welt on Lambda

Deploy Welt's `template.yaml` following the [Lambda guide](lambda.md). Supply its Slack credentials and these target parameters:

| Mode | `AgentArn` | `AgentGatewayUrl` | `GatewayArn` |
| --- | --- | --- | --- |
| Gateway URL | Empty | `AgentGatewayUrl` stack output | `GatewayArn` stack output |
| Runtime SDK through Gateway | Runtime ARN | `AgentGatewayUrl` stack output | `GatewayArn` stack output |

The template grants Gateway invocation permission in both modes. It adds Runtime invocation permission when `AgentArn` is supplied.

## Verify the connection

In Slack, ask the agent to call a tool and return a file. For an agent with interrupt support, ask for an action requiring approval, then answer its question and check that the same run resumes. Check both approval and rejection.

To confirm traffic reaches the Gateway, open CloudWatch metrics in its region and select `AWS/Bedrock-AgentCore`, `Invocations`, the Gateway ARN as `Resource`, `InvokeGateway` as `Operation`, and `HTTP` as `Protocol`. View the sum over one-minute periods. See [Gateway metrics](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/observability-gateway-metrics.html).

## Remove the Gateway

To invoke the Runtime directly, set `AGENT_ARN`, remove `AGENT_GATEWAY_URL`, and restart Welt. On Lambda, supply `AgentArn` and deploy with both `AgentGatewayUrl` and `GatewayArn` empty. Then delete the Gateway stack:

```sh
aws cloudformation delete-stack \
  --region <runtime-region> \
  --stack-name welt-gateway-example

aws cloudformation wait stack-delete-complete \
  --region <runtime-region> \
  --stack-name welt-gateway-example
```

See [AWS's HTTP Runtime target documentation](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-target-http-runtime.html) for Gateway configuration and routing.
