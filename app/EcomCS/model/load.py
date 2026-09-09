import os
from dotenv import load_dotenv

from strands.models.bedrock import BedrockModel

# Load environment variables from
load_dotenv()
model_id = os.getenv("LLM_MODELS")


def load_model() -> BedrockModel:
    """Get Bedrock model client using IAM credentials."""
    return BedrockModel(model_id=model_id)
