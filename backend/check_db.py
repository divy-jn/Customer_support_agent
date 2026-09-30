import os
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()
url = os.environ.get("SUPABASE_URL")
key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", os.environ.get("SUPABASE_KEY"))

supabase = create_client(url, key)

res = supabase.table("conversations").select("*").execute()
rows = res.data

if rows:
    print(rows[-1].keys())
    
    print("\nAll non-empty sessions:")
    for r in rows:
        history = r.get("transcript") or []
        if history:
            print(f"Session: {r['session_id']}, Mode: {r.get('mode')}")
            for msg in history[-2:]:
                print(f"  {msg.get('role')}: {msg.get('content')}")
