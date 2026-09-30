import asyncio
import websockets
import json

async def monitor():
    # Try to connect to a0394907-b8cb-4c3f-b10b-b69d84ed68e0 which was logged as accepted
    uri = "ws://127.0.0.1:8000/ws/agent/a0394907-b8cb-4c3f-b10b-b69d84ed68e0?agent_secret=test-agent-secret"
    try:
        async with websockets.connect(uri) as ws:
            print("Connected as agent!")
            welcome = await ws.recv()
            print("Welcome:", welcome)
            while True:
                msg = await asyncio.wait_for(ws.recv(), timeout=2.0)
                print("Received:", msg)
    except Exception as e:
        print("Error:", e)

asyncio.run(monitor())
