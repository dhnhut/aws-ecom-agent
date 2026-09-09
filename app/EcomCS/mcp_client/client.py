import os
import logging
from mcp.client.streamable_http import streamablehttp_client
from strands.tools.mcp.mcp_client import MCPClient

from dotenv import load_dotenv
load_dotenv()

logger = logging.getLogger(__name__)

# ExaAI provides information about code through web searches, crawling and code context searches through their platform. Requires no authentication
EXAMPLE_MCP_ENDPOINT = "https://mcp.exa.ai/mcp"

GATEWAY_ENDPOINT = os.environ.get(
    "GATEWAY_ENDPOINT")

if not GATEWAY_ENDPOINT:
    raise ValueError(
        "GATEWAY_ENDPOINT environment variable is not set. Please set it in the .env file.")


def get_streamable_http_mcp_client() -> MCPClient:
    """Returns an MCP Client compatible with Strands"""
    # to use an MCP server that supports bearer authentication, add headers={"Authorization": f"Bearer {access_token}"}
    return MCPClient(lambda: streamablehttp_client(EXAMPLE_MCP_ENDPOINT))


def get_business_mcp_client() -> MCPClient:
    """Returns an MCP Client compatible with Strands"""
    # to use an MCP server that supports bearer authentication, add headers={"Authorization": f"Bearer {access_token}"}
    return MCPClient(lambda: streamablehttp_client(GATEWAY_ENDPOINT))
