import os
import logging
from mcp.client.streamable_http import streamablehttp_client
from strands.tools.mcp.mcp_client import MCPClient

from dotenv import load_dotenv
load_dotenv()

logger = logging.getLogger(__name__)

# ExaAI provides information about code through web searches, crawling and code context searches through their platform. Requires no authentication
EXAMPLE_MCP_ENDPOINT = "https://mcp.exa.ai/mcp"

# Name of the AgentCore gateway declared in agentcore/agentcore.json. Its URL and
# auth type are injected as AGENTCORE_GATEWAY_<NAME>_URL / _AUTH_TYPE — by the CDK
# when deployed, and by `agentcore dev` from .cli/deployed-state.json — so there is
# nothing to put in .env.
GATEWAY_NAME = "CustomerSupportGateway"


def _gateway_env(suffix: str) -> str:
    prefix = f"AGENTCORE_GATEWAY_{GATEWAY_NAME.upper().replace('-', '_')}"
    return f"{prefix}_{suffix}"


def get_streamable_http_mcp_client() -> MCPClient:
    """Returns an MCP Client compatible with Strands"""
    # to use an MCP server that supports bearer authentication, add headers={"Authorization": f"Bearer {access_token}"}
    return MCPClient(lambda: streamablehttp_client(EXAMPLE_MCP_ENDPOINT))


def get_gateway_mcp_client() -> MCPClient | None:
    """Returns an MCP Client for the AgentCore gateway, or None when unavailable.

    The URL only exists once the gateway is deployed, so this returns None before
    the first deploy and the agent simply runs without gateway tools.
    """
    url = os.environ.get(_gateway_env("URL"))
    if not url:
        logger.warning("%s is not set — skipping gateway tools",
                       _gateway_env("URL"))
        return None

    auth_type = os.environ.get(_gateway_env("AUTH_TYPE"), "NONE")
    if auth_type != "NONE":
        # AWS_IAM needs SigV4-signed requests and CUSTOM_JWT needs a bearer token
        # fetched from the credential provider; neither is wired up here yet.
        logger.warning(
            "gateway %s uses %s auth, which this client does not sign — skipping gateway tools",
            GATEWAY_NAME,
            auth_type,
        )
        return None

    logger.info("connecting to gateway %s at %s", GATEWAY_NAME, url)
    return MCPClient(lambda: streamablehttp_client(url))
