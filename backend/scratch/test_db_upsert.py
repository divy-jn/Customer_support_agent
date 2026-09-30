import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from app.tools import supabase
import uuid

session_id = "test-upsert-" + uuid.uuid4().hex[:6]
data = {
    "session_id": session_id,
    "customer_id": 1,
    "transcript": [{"role": "customer", "content": "hello"}],
    "escalated": False
}
print("Insert first time...")
res = supabase.table("conversations").upsert(data, on_conflict="session_id").execute()
print(res.data)

print("Upsert second time...")
data["transcript"].append({"role": "agent", "content": "hi"})
res2 = supabase.table("conversations").upsert(data, on_conflict="session_id").execute()
print(res2.data)

print("Fetch...")
res3 = supabase.table("conversations").select("*").eq("session_id", session_id).execute()
print(res3.data)
