import os
import logging
from urllib.parse import urlparse

import boto3
import httpx
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest

from dotenv import load_dotenv
load_dotenv()

logger = logging.getLogger(__name__)

# ── SigV4 request signing for the AgentCore Gateway ───────────────────────────
#
# The gateway is configured with `"authorizerType": "AWS_IAM"` in
# agentcore/agentcore.json. That means it accepts a request only if the request
# carries a valid AWS SigV4 signature AND the IAM identity behind that signature
# is allowed to call `bedrock-agentcore:InvokeGateway` on this gateway's ARN.
#
# Nothing here needs a token, a secret, or a login. AWS credentials already exist
# wherever this code runs:
#   - in the AgentCore runtime, from the container's execution role (the CDK adds
#     the InvokeGateway grant to that role because authorizerType is AWS_IAM)
#   - on a laptop, from `aws configure` / SSO / whatever the AWS CLI uses
# So "authentication" here is purely: sign each outgoing HTTP request.
#
# IMPORTANT: this must stay in sync with authorizerType in agentcore.json.
# If that ever goes back to NONE, these signatures are ignored; if it becomes
# CUSTOM_JWT, signatures are rejected and a bearer token is needed instead.

# SigV4 signatures are scoped to an (AWS service, region) pair, and the string
# has to match what the service expects byte for byte.
# The default is the real service name and is what production uses; the env var
# is only an escape hatch. Never let this resolve to None — SigV4 would happily
# sign with a credential scope of ".../None/aws4_request" and every request would
# come back 403 with nothing in the message pointing here.
SIGV4_SERVICE = os.environ.get("SIGV4_SERVICE", "bedrock-agentcore")

# One boto3 Session for the whole process. Creating a Session walks the whole
# credential chain (env vars, config file, container credential endpoint, ...),
# which is slow, so we do it once at import rather than per request.
_boto_session = boto3.Session()


def gateway_region(endpoint: str) -> str:
    """Work out which AWS region to sign for, from the gateway URL itself.

    A gateway URL looks like:
        https://<gateway-id>.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp
                                     └─ SIGV4_SERVICE ─┘ └─ region ─┘

    Reading the region out of the URL is more reliable than trusting AWS_REGION:
    the signature has to name the region the *gateway* lives in, which is not
    necessarily the region this process is configured for.
    """
    host = urlparse(endpoint).hostname or ""
    labels = host.split(".")
    if SIGV4_SERVICE in labels:
        service_at = labels.index(SIGV4_SERVICE)
        # The region is the label immediately after the service name.
        if service_at + 1 < len(labels):
            return labels[service_at + 1]

    # Fall back to the ambient region if the URL is not in the expected shape.
    region = _boto_session.region_name
    if not region:
        raise ValueError(
            f"Could not determine the AWS region for gateway endpoint {endpoint!r}, "
            "and no default region is configured. Set AWS_REGION.")
    logger.warning(
        "Could not parse a region out of the gateway endpoint; falling back to %s", region)
    return region


class SigV4HttpxAuth(httpx.Auth):
    """Signs every outgoing httpx request with AWS SigV4.

    httpx calls `auth_flow` once per request and sends whatever we yield, so this
    runs for each individual MCP message: the POSTs that carry JSON-RPC calls,
    the GET that opens the server-sent-events stream, and the DELETE that ends
    the session. Signing per request (rather than obtaining one token up front)
    is what makes this safe in a long-lived container: an AWS signature is valid
    for only a few minutes, and a fresh one is minted every time.
    """

    # Tells httpx to fully read the request body *before* calling auth_flow.
    # SigV4 hashes the payload as part of the signature, so we cannot sign a body
    # that has not been read yet. Without this flag, request.content may be empty
    # and every signature would be wrong.
    requires_request_body = True

    def __init__(self, service: str, region: str):
        self._service = service
        self._region = region
        self._credentials = None

    def _get_credentials(self):
        """Resolve credentials once, then reuse the resolver object.

        `get_credentials()` returns a *refreshable* credentials object for roles
        (container role, EC2 instance profile, assumed role). Holding onto it and
        calling `get_frozen_credentials()` per request is what lets expiring
        credentials renew themselves quietly in the background — which is exactly
        what a container that lives for hours needs.

        Caveat: resolving (and later refreshing) credentials can make a blocking
        network call to the container credential endpoint, and auth_flow runs on
        the event loop. It happens once at startup and roughly hourly after that,
        so the stall is rare and short. If it ever shows up in latency numbers,
        the fix is to override `async_auth_flow` and do this part in a thread.
        """
        if self._credentials is None:
            self._credentials = _boto_session.get_credentials()
            if self._credentials is None:
                raise RuntimeError(
                    "No AWS credentials found, so requests to the AgentCore Gateway "
                    "cannot be signed. In the AgentCore runtime this comes from the "
                    "execution role; locally, configure the AWS CLI.")
        return self._credentials

    def auth_flow(self, request: httpx.Request):
        # Snapshot the credentials for this one request (refreshing them first if
        # they are close to expiring).
        frozen = self._get_credentials().get_frozen_credentials()

        # botocore signs its own AWSRequest type, not httpx's, so build a
        # throwaway AWSRequest that mirrors the real request's method, URL and
        # body — the three things SigV4 hashes.
        #
        # We deliberately pass no headers. SigV4 records which headers it signed
        # in the signature's own SignedHeaders list, so signing only the minimum
        # (host, plus the x-amz-* headers botocore adds itself) is valid. It also
        # avoids a whole class of bug: if we signed a header that httpx later
        # rewrote — content-length, say — the signature would no longer match the
        # request that arrives and the gateway would reject it.
        aws_request = AWSRequest(
            method=request.method,
            url=str(request.url),
            data=request.content,
        )

        # add_auth() computes the signature and writes the auth headers onto
        # aws_request.headers.
        SigV4Auth(frozen, self._service, self._region).add_auth(aws_request)

        # Copy those headers onto the real request. X-Amz-Security-Token is only
        # present for temporary credentials (roles, SSO); long-lived IAM user keys
        # have no session token, hence the `if`.
        for header in ("Authorization", "X-Amz-Date", "X-Amz-Security-Token"):
            value = aws_request.headers.get(header)
            if value is not None:
                request.headers[header] = value

        # Yielding the request hands it back to httpx to actually send. A single
        # yield means "no retry logic" — we are not waiting for a 401 to react to.
        yield request
