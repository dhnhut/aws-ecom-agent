import os
import uuid
from typing import Optional

from bedrock_agentcore.memory.integrations.strands.config import AgentCoreMemoryConfig, RetrievalConfig
from bedrock_agentcore.memory.integrations.strands.session_manager import AgentCoreMemorySessionManager

MEMORY_ID = os.getenv("MEMORY_ECOMCSMEMORY_ID")
REGION = os.getenv("AWS_REGION")


def get_memory_session_manager(session_id: Optional[str], actor_id: str) -> Optional[AgentCoreMemorySessionManager]:
    if not MEMORY_ID:
        return None

    # AgentCoreMemoryConfig rejects None; OAuth/CUSTOM_JWT callers can reach us
    # without a runtime session header, so synthesize one when absent.
    session_id = session_id or uuid.uuid4().hex

    # Relevance scores in this store cluster in the 0.34-0.40 band, so a stable
    # identity fact ("the user's name is ...") ranks below incidental order chatter
    # on most queries. Keep top_k generous or the durable facts get dropped.
    retrieval_config = {
        f"/users/{actor_id}/facts": RetrievalConfig(top_k=10, relevance_score=0.1),
        f"/users/{actor_id}/preferences": RetrievalConfig(top_k=8, relevance_score=0.1),
        # The episodic strategy writes raw episodes per session and reflections to
        # /episodes/{actorId}; only the reflections survive across sessions.
        f"/episodes/{actor_id}": RetrievalConfig(top_k=5, relevance_score=0.1),
        # SUMMARIZATION writes to /summaries/{actorId}/{sessionId} - the session
        # segment is part of the namespace and retrieval does not prefix-match.
        f"/summaries/{actor_id}/{session_id}": RetrievalConfig(top_k=3, relevance_score=0.1),
    }

    return AgentCoreMemorySessionManager(
        AgentCoreMemoryConfig(
            memory_id=MEMORY_ID,
            session_id=session_id,
            actor_id=actor_id,
            retrieval_config=retrieval_config,
            async_mode=True,
        ),
        REGION
    )
