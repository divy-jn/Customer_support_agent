import asyncio
import websockets
import json
import jwt
from datetime import datetime, timedelta

async def test():
    token = jwt.encode({'sub': '151', 'role': 'customer', 'exp': datetime.utcnow() + timedelta(hours=1)}, 'my_super_secret_jwt_key_123', algorithm='HS256')
    uri = f"ws://localhost:8002/ws/chat/test-session-123?token={token}"
    try:
        async with websockets.connect(uri) as ws:
            msg = await ws.recv()
            print("Received:", msg)
            await ws.send(json.dumps({'message': 'hello', 'customer_id': 151}))
            while True:
                response = await ws.recv()
                print("Response:", response)
    except Exception as e:
        print("Error:", e)

asyncio.run(test())
