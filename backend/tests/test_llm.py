import asyncio
from dotenv import load_dotenv

# Load env vars before importing app modules
load_dotenv()

from app.config import settings
from app.llm_factory import get_llm


async def main():
    print("Testing Vertex AI LLM Factory...")
    print(f"Model       : {settings.llm_model}")
    print(f"GCP Project : {settings.vertex_project_id}")
    print(f"Location    : {settings.vertex_location}")

    try:
        print(f"\n--- Testing Primary Model ({settings.llm_model}) ---")
        llm = get_llm()
        response = await llm.ainvoke("Say 'Hello from Vertex AI Gemini!'")
        print("Response:", response.content)
    except Exception as e:
        print(f"Error invoking model: {e}")


if __name__ == "__main__":
    asyncio.run(main())
