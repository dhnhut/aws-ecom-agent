# AgentCore Project

This project aim to build a customer support chatbot using AWS Bedrock AgentCore.

The agent can access tool through Bedrock Gateway, Knowledgebase, Longterm memory, and searching the internet.

## Project Structure

This project was initially created with the [AgentCore CLI](https://github.com/aws/agentcore-cli).

```
aws-ecom-agent/
├── .devcontainer /         # vs code devcontainer env
├── AGENTS.md               # AI coding assistant context
├── agentcore/
│   ├── agentcore.json      # Project config (agents, memories, credentials, gateways, evaluators)
│   ├── aws-targets.json    # Deployment targets (account + region)
│   ├── .env.local          # Secrets — API keys (gitignored)
│   ├── .llm-context/       # TypeScript type definitions for AI assistants
│   │   ├── agentcore.ts    # AgentCoreProjectSpec types
│   │   └── aws-targets.ts  # Deployment target types
│   └── cdk/                # CDK infrastructure (@aws/agentcore-cdk)
├── app/                    # Agent application code
├── evaluators/             # Custom evaluator code (if any)
├── lambda/                 # Business logic
└── infra/                  # Infrastructure outside of AgentCore
```

## Getting Started

### Prerequisites

- **Node.js** 20.x or later
- **Python 3.10+** and **uv** for Python agents ([install uv](https://docs.astral.sh/uv/getting-started/installation/))
- **AWS credentials** configured (`aws configure` or environment variables)
- **Docker** (only for Container build agents)


## Support Infrastructure

### Lambda functions for tool

- `order-tracker` — deployed by `infra/` (see below), reached through API Gateway
- `refund-processor` — deployed by AgentCore as a `lambda` gateway target

### APIGateway

A REST API that proxies to the `order-tracker` Lambda with three routes:

| Method and Resource | Operation Name |
| -- | -- |
| `GET /orders/{order_id}` | `get_order` |
| `GET /customers/{customer_id}/orders` | `get_customer_orders` |
| `GET /customers/{customer_id}` | `get_customer` |

This lives outside AgentCore because an AgentCore gateway target of type
`apiGateway` can only attach to an *existing* REST API — it takes `restApiId`
and `stage` and never provisions one.

### `infra/` stack

Plain CloudFormation (no CDK), covering the `order-tracker` Lambda, its IAM role
and log group, the REST API, and the `prod` stage.

| File | Purpose |
| -- | -- |
| `infra/order-tracker-api.yaml` | The CloudFormation template |
| `infra/deploy.sh` | Zips the Lambda, uploads it to S3, deploys the stack |

```bash
./infra/deploy.sh
```

The Lambda source is over the 4096-byte limit for inline `Code.ZipFile`, so the
template reads it from S3; `deploy.sh` handles packaging, creates the artifact
bucket on first run, and prints the stack outputs.

- `STACK_NAME`=`ecomcs-extended-infra`
- `STAGE_NAME`=`prod`
- `PROJECT_NAME`=`EcomCS-Extra-Infra`
- `API_AUTH_TYPE`=`NONE`
- `ARTIFACT_BUCKET`=`ecom-agent-extended-artifacts-${ACCOUNT_ID}-${AWS_REGION}`
- `ACCOUNT_ID` and `AWS_REGION` are read from `agentcore/aws-targets.json`

An API Gateway deployment + stage is an immutable snapshot of the routes. When you add
or change a route, bump the `ApiDeploymentV1` logical ID in the template so a
fresh snapshot is created and the stage repoints at it.

Wire the stack outputs into the AgentCore gateway target:

```jsonc
{
  "name": "order-tracker",
  "targetType": "apiGateway",
  "apiGateway": {
    "restApiId": "<RestApiId output>",
    "stage": "<StageName output>",
    "apiGatewayToolConfiguration": { "toolFilters": [ /* ... */ ] }
  }
}
```

## Agentcore CLI

### Development

Run your agent locally:

```bash
agentcore dev
```

### Validate Invocation Input

Validate runtime invocation payloads before forwarding them to an agent framework. Keep user prompts typed as strings
and pass only prompt text to the agent.

### Deployment

Deploy to AWS:

```bash
agentcore deploy
```

## Commands

| Command | Description |
| --- | --- |
| `agentcore create` | Create a new AgentCore project |
| `agentcore add` | Add resources (agent, memory, credential, gateway, evaluator, policy) |
| `agentcore remove` | Remove resources |
| `agentcore dev` | Run agent locally with hot-reload |
| `agentcore deploy` | Deploy to AWS via CDK |
| `agentcore status` | Show deployment status |
| `agentcore invoke` | Invoke agent (local or deployed) |
| `agentcore logs` | View agent logs |
| `agentcore traces` | View agent traces |
| `agentcore eval` | Run evaluations |
| `agentcore package` | Package agent artifacts |
| `agentcore validate` | Validate configuration |
| `agentcore pause` | Pause a deployed agent |
| `agentcore resume` | Resume a paused agent |
| `agentcore fetch` | Fetch remote resource definitions |
| `agentcore import` | Import existing resources |
| `agentcore update` | Check for CLI updates |

## Configuration

Edit the JSON files in `agentcore/` to configure your project. See `agentcore/.llm-context/` for type definitions and validation constraints.

The project uses a **flat resource model** — agents, memories, credentials, gateways, evaluators, and policies are top-level arrays in `agentcore.json`. Resources are independent; agents discover memories and credentials at runtime via environment variables or SDK calls.

## Resources

| Resource | Purpose |
| --- | --- |
| Agent (runtime) | HTTP, MCP, or A2A agent deployed to AgentCore Runtime |
| Memory | Persistent context storage with configurable strategies |
| Credential | API key or OAuth credential providers |
| Gateway | MCP gateway that routes tool calls to targets |
| Gateway Target | Tool implementation (Lambda, MCP server, OpenAPI, Smithy, API Gateway) |
| Evaluator | Custom LLM-as-a-Judge or code-based evaluation |
| Online Eval Config | Continuous evaluation pipeline for deployed agents |
| Policy | Cedar authorization policies for gateway tools |

### Agent Types

- **Template agents**: Created from framework templates (Strands, LangChain/LangGraph, GoogleADK, OpenAI Agents, Autogen)
- **BYO agents**: Bring your own code with `agentcore add agent --type byo`
- **Import agents**: Import existing Bedrock agents with `agentcore import`

### Build Types

- **CodeZip**: Python source packaged as a zip and deployed directly to AgentCore Runtime
- **Container**: Docker image built via CodeBuild (ARM64), pushed to ECR, and deployed to AgentCore Runtime

## Documentation

- [AgentCore CLI](https://github.com/aws/agentcore-cli)
- [AgentCore CDK Constructs](https://github.com/aws/agentcore-l3-cdk-constructs)
- [Amazon Bedrock AgentCore](https://aws.amazon.com/bedrock/agentcore/)
