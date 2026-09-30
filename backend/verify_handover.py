import asyncio
import websockets
import json

async def test_handover():
    session_id = "verify_session_999"
    customer_id = 1
    
    uri_customer = f"ws://localhost:8000/ws/chat/{session_id}?customer_id={customer_id}"
    uri_agent = f"ws://localhost:8000/ws/agent/{session_id}"
    
    print("Connecting...")
    async with websockets.connect(uri_customer) as ws_cust, \
               websockets.connect(uri_agent) as ws_agent:
        
        # Empty out initial connection messages
        print("Connected.")
        
        # 1. Customer says "connect me with human"
        print("\n--- PHASE 1: AI TO HUMAN ESCALATION ---")
        print("Customer: connect me with human")
        await ws_cust.send("connect me with human")
        
        # Wait for AI response to customer
        res1 = await ws_cust.recv()
        print(f"Cust Received (AI Typing): {res1}")
        res2 = await ws_cust.recv()
        print(f"Cust Received (AI Response): {res2}")
        
        # Agent receives the message
        while True:
            agent_msg = await ws_agent.recv()
            print(f"Agent Received Event: {agent_msg}")
            data = json.loads(agent_msg)
            if data.get("type") == "escalation_alert" or "connect me with human" in str(data):
                break
        
        # 2. Agent takes over
        print("\n--- PHASE 2: HUMAN TAKEOVER ---")
        print("Agent: takeover")
        await ws_agent.send(json.dumps({"type": "takeover", "agent_name": "Test Agent"}))
        
        # Customer should get system message
        sys_msg = await ws_cust.recv()
        print(f"Cust Received (System): {sys_msg}")
        
        # 3. Customer says "hello"
        print("\nCustomer: hello")
        await ws_cust.send("hello")
        
        # Agent receives "hello"
        agent_msg2 = await ws_agent.recv()
        print(f"Agent Received Event: {agent_msg2}")
        
        # 4. Agent says "Hi, I'm checking this for you."
        print("\nAgent: Hi, I'm checking this for you.")
        await ws_agent.send(json.dumps({"type": "agent_message", "message": "Hi, I'm checking this for you.", "agent_name": "Test Agent"}))
        
        # Customer receives agent message
        cust_msg2 = await ws_cust.recv()
        print(f"Cust Received (Human): {cust_msg2}")
        
        # 5. Customer says "are you there?"
        print("\nCustomer: are you there?")
        await ws_cust.send("are you there?")
        
        agent_msg3 = await ws_agent.recv()
        print(f"Agent Received Event: {agent_msg3}")
        
        # Wait briefly to ensure AI does NOT respond (it should time out on recv if AI is bypassed)
        print("Waiting 3 seconds to confirm AI does NOT respond...")
        try:
            ai_res = await asyncio.wait_for(ws_cust.recv(), timeout=3.0)
            print(f"FAILURE: AI RESPONDED: {ai_res}")
        except asyncio.TimeoutError:
            print("SUCCESS: AI did not respond during human mode.")
            
        
        # 6. Agent says "Yes, I'm here."
        print("\nAgent: Yes, I'm here.")
        await ws_agent.send(json.dumps({"type": "agent_message", "message": "Yes, I'm here.", "agent_name": "Test Agent"}))
        
        cust_msg3 = await ws_cust.recv()
        print(f"Cust Received (Human): {cust_msg3}")
        
        # 7. Agent releases
        print("\n--- PHASE 3: RELEASE TO AI ---")
        print("Agent: release")
        await ws_agent.send(json.dumps({"type": "release"}))
        
        cust_sys2 = await ws_cust.recv()
        print(f"Cust Received (System): {cust_sys2}")
        
        # 8. Customer asks question to AI
        print("\nCustomer: what is my order status?")
        await ws_cust.send("what is my order status?")
        
        final_res = await ws_cust.recv()
        print(f"Cust Received (AI Typing): {final_res}")
        final_res2 = await ws_cust.recv()
        print(f"Cust Received (AI Response): {final_res2}")

if __name__ == "__main__":
    asyncio.run(test_handover())
