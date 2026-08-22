import asyncio
import os
import sys
import logging

logging.basicConfig(level=logging.INFO)

# Ensure backend path is in sys.path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv()

from app.agents.graph import customer_support_graph

async def test_query(message: str):
    print(f"\n[{'='*50}]")
    print(f"QUERY: {message}")
    
    state = {
        "customer_id": 1,
        "session_id": "test_session_123",
        "message": message,
        "conversation_history": []
    }
    
    try:
        # Run graph asynchronously with timeout
        result = await asyncio.wait_for(customer_support_graph.ainvoke(state), timeout=15.0)
        print(f"ROUTED TO: {result.get('route_to', 'unknown')}")
        print(f"INTENT: {result.get('intent', 'unknown')}")
        print(f"RESPONSE:\n{result.get('response', 'NO RESPONSE')}")
    except asyncio.TimeoutError:
        print("ERROR: Graph execution timed out after 15 seconds.")
    except Exception as e:
        print(f"ERROR: {e}")

async def main():
    queries = [
        "What is your return policy?",
        "Where is my order #12345?"
    ]
    for q in queries:
        await test_query(q)

if __name__ == "__main__":
    asyncio.run(main())
