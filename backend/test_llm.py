import asyncio
from dotenv import load_dotenv

# Load env vars before importing app modules
load_dotenv()

from app.llm_factory import get_llm

async def main():
    print("Testing LLM Factory...")
    
    # Test large model
    try:
        print("\n--- Testing Large Model ---")
        llm_large = get_llm(model="gpt-oss:120b-cloud")
        response_large = await llm_large.ainvoke("Say 'Hello, Large Model!'")
        print("Response:", response_large.content)
    except Exception as e:
        print(f"Error with large model: {e}")

    # Test small model
    try:
        print("\n--- Testing Small Model ---")
        llm_small = get_llm(model="gpt-oss:20b-cloud")
        response_small = await llm_small.ainvoke("Say 'Hello, Small Model!'")
        print("Response:", response_small.content)
    except Exception as e:
        print(f"Error with small model: {e}")

if __name__ == "__main__":
    asyncio.run(main())
