from typing import Any
from collections import OrderedDict
from strands import Agent, tool
import asyncio
import json
import os
from strands.agent.conversation_manager.null_conversation_manager import NullConversationManager
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from bedrock_agentcore.tools.code_interpreter_client import code_session

# ── TODO 3 — Model and Clients ────────────────────────────────────────────────
from model.load import load_model
from mcp_client.client import get_gateway_mcp_client, get_streamable_http_mcp_client

from memory.session import get_memory_session_manager

# ── TODO 1 — App Initialisation ───────────────────────────────────────────────
app = BedrockAgentCoreApp()
log = app.logger

# ── TODO 2 — Configuration ────────────────────────────────────────────────────
# prefer agentcore/agentcore.json for all configuration

# AWS_REGION is set by the runtime container; local runs may have neither, and the
# deploy target in agentcore/aws-targets.json is us-east-1.
REGION = os.getenv("AWS_REGION") or os.getenv(
    "AWS_DEFAULT_REGION") or "us-east-1"

# Define a Streamable HTTP MCP Client
mcp_clients = [get_streamable_http_mcp_client(), get_gateway_mcp_client()]

DEFAULT_SYSTEM_PROMPT = """
# You are an intelligent customer support assistant for an e-commerce platform name Ecom.

## Use tools when appropriate.

### Knowledge Base
Product, policy, loyalty member, and troubleshooting questions are answered from the
CustomerSupportKB knowledge base, reachable as the `customer-support-kb___Retrieve`
and `customer-support-kb___AgenticRetrieveStream` tools on the support gateway.
Retrieve before you answer any question about product specs, pricing, warranty
terms, the return or refund policy, shipping, or how to fix a device — never
answer those from your own knowledge. Prefer `Retrieve` for a single lookup and
`AgenticRetrieveStream` for questions that span several documents.

Ground your answer in the retrieved passages and cite the product or policy name
you drew it from. If retrieval comes back with nothing relevant, say the
knowledge base does not cover it rather than guessing.

### Loyalty Discounts: always compute with the tool
Use the `calculate_loyalty_discount` tool whenever a customer asks what a discount,
redemption, or final price actually works out to — never do the arithmetic yourself.
The knowledge base is for describing the program (tier thresholds, benefits, earn and
redemption rules); the tool is for any concrete number. Report its figures as returned.

### Order Lookups, Refunds and Return Labels: go to their own tools

## You have PERSISTENT MEMORY: you remember each customer's preferences, past product, orders,
and interests across multiple conversations.

MEMORY-AWARE BEHAVIOUR
- When a <user_context> block appears at the start of the user's message, it contains facts and preferences retrieved from past conversations.
- Use this context to personalise your recommendations naturally.
- Reference past context: "Based on your interest in Speakers..."
- Never ask the Customer to repeat information they've already shared.

Be warm, attentive, and genuinely helpful — like a trusted assistant who has known the customer for years.
"""


# Define a collection of tools used by the model
tools = []


# Add MCP client to tools if available
for mcp_client in mcp_clients:
    if mcp_client:
        tools.append(mcp_client)


def _make_conversation_manager():
    return NullConversationManager()


def agent_factory():
    cache = {}

    def get_or_create_agent(session_id, user_id):
        _actor_id = user_id
        key = f"{session_id}/{_actor_id}"
        if key not in cache:
            cache[key] = Agent(
                model=load_model(),
                session_manager=get_memory_session_manager(
                    session_id, _actor_id),
                conversation_manager=_make_conversation_manager(),
                system_prompt=DEFAULT_SYSTEM_PROMPT,
                tools=tools,
                hooks=[
                ],
            )
        return cache[key]
    return get_or_create_agent


get_or_create_agent = agent_factory()


def strip_trailing_tool_use(messages: Any) -> list[dict]:
    """Strip toolUse blocks from the tail until the last message has none."""
    if not isinstance(messages, list):
        raise ValueError("messages must be a list")

    messages = list(messages)
    while messages:
        last = messages[-1]
        if not isinstance(last, dict):
            raise ValueError("each message must be an object")
        original_content = last.get("content", [])
        if not isinstance(original_content, list) or not all(isinstance(block, dict) for block in original_content):
            raise ValueError(
                "each message content value must be a list of content blocks")

        content = [block for block in original_content if "toolUse" not in block]
        if len(content) == len(original_content):
            break
        if content:
            messages[-1] = {**last, "content": content}
            break
        messages.pop()

    return messages


def _extract_prompt(payload: dict):
    """Accept validated harness messages, tool results, or a plain prompt string."""
    if not isinstance(payload, dict):
        raise ValueError("payload must be a JSON object")
    if "messages" in payload:
        return strip_trailing_tool_use(payload["messages"])
    if "tool_results" in payload:
        tool_results = payload["tool_results"]
        if not isinstance(tool_results, list) or not all(
            isinstance(tool_result, dict) and isinstance(
                tool_result.get("toolUseId"), str)
            for tool_result in tool_results
        ):
            raise ValueError(
                "tool_results must contain objects with a toolUseId string")
        return [{"role": "user", "content": [{"toolResult": {
            "toolUseId": tr["toolUseId"],
            "status": tr.get("status", "success"),
            "content": tr.get("content", []),
        }} for tr in tool_results]}]
    prompt = payload.get("prompt", "")
    if not isinstance(prompt, str):
        raise ValueError("prompt must be a string")
    return prompt


# ── Loyalty Discount Tool (Code Interpreter) ─────────────────────────────────
# The redemption and earn rules below mirror the Loyalty Rewards Program section
# of the knowledge base (data/product_catalog.txt): 100 points = $1, minimum
# redemption 500 points, earn rates 1/2/5 per dollar, Gold 10% / Platinum 15%.
# Keep the two in sync — the agent can answer loyalty questions from either.

# Shared with the fallback path so both compute from one definition.
_EARN_RATES = {"standard": 1, "device": 2, "fresh": 5}
_TIER_RATES = {"Silver": 0.00, "Gold": 0.10, "Platinum": 0.15}


@tool
def calculate_loyalty_discount(
    loyalty_points: int,
    tier: str,
    order_total: float,
    product_category: str = "standard",
) -> str:
    """
    Calculate the loyalty discount for a customer order using the
    AgentCore Code Interpreter. Runs exact arithmetic in a secure sandbox.

    Args:
        loyalty_points:   Customer's current points balance
        tier:             Customer tier — Silver, Gold, or Platinum
        order_total:      Order total in USD
        product_category: standard, device, or fresh

    Returns:
        Full discount breakdown and final price
    """
    # Literal braces are doubled — this is an f-string injecting the arguments.
    code = f"""
import json

earn_rates = {{"standard": 1, "device": 2, "fresh": 5}}
tier_rates = {{"Silver": 0.00, "Gold": 0.10, "Platinum": 0.15}}

loyalty_points = {int(loyalty_points)}
tier = {str(tier)!r}
order_total = {float(order_total)}
product_category = {str(product_category)!r}

tier_key = tier.strip().title()
category_key = product_category.strip().lower()

# 100 points = $1, and redemption happens in 500-point blocks.
usable_points = (max(loyalty_points, 0) // 500) * 500
# Points may cover at most 50% of the order, also floored to a 500 block.
cap_points = (int(max(order_total, 0.0) * 0.5 * 100) // 500) * 500
points_redeemed = min(usable_points, cap_points)
points_value = round(points_redeemed / 100, 2)

subtotal = round(order_total - points_value, 2)
tier_discount_pct = tier_rates.get(tier_key, 0.0)
tier_discount = round(subtotal * tier_discount_pct, 2)

final_total = round(subtotal - tier_discount, 2)
total_savings = round(order_total - final_total, 2)
points_earned = int(final_total * earn_rates.get(category_key, 1))
remaining_points = loyalty_points - points_redeemed

result = {{
    "order_total": order_total,
    "tier": tier_key,
    "points_redeemed": points_redeemed,
    "points_value": points_value,
    "subtotal_after_points": subtotal,
    "tier_discount_pct": round(tier_discount_pct * 100, 2),
    "tier_discount": tier_discount,
    "final_total": final_total,
    "total_savings": total_savings,
    "points_earned": points_earned,
    "remaining_points": remaining_points,
    "computed_by": "code_interpreter",
}}
print(json.dumps(result))
"""

    with code_session(REGION) as client:
        response = client.invoke(
            "executeCode",
            {
                "code": code,
                "language": "python",
                "clearContext": True,
            },
        )
        for event in response["stream"]:
            return json.dumps(event["result"])
    # An empty stream means no result came back — treat it like any other
    # sandbox failure rather than returning None to the agent.
    raise RuntimeError("code interpreter returned no result events")


tools.append(calculate_loyalty_discount)


@app.entrypoint
async def invoke(payload, context):
    log.info("Invoking Agent.....")
    log.info("Payload: %s", payload)
    log.info("Context: %s", context)

    session_id = getattr(context, 'session_id', 'default-session')
    user_id = payload.get("user_id") or getattr(
        context, "user_id", None) or "default-user"

    agent = get_or_create_agent(session_id, user_id)

    prompt = _extract_prompt(payload)

    async for event in agent.stream_async(
        prompt,
    ):
        if not isinstance(event, dict) or "event" not in event:
            continue
        cbs = event["event"].get("contentBlockStart")
        if cbs is not None and not cbs.get("start"):
            continue
        yield event


if __name__ == "__main__":
    app.run()
