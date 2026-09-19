"""
Seed the Supabase database with deterministic Indian synthetic data.

Uses the generate_indian_dataset module for consistent, reproducible data.
Properly chains foreign keys: products → customers → orders → tickets.

Usage:
    cd backend
    python -m scripts.seed_database
"""

import sys
import os
from datetime import datetime, timedelta, timezone

from supabase import create_client, Client

# Add parent dir to path so we can import app modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.config import settings
from scripts.generate_indian_dataset import generate_full_dataset


def seed():
    """Main seeding function."""
    print("=" * 60)
    print("🇮🇳  IntelliSupport — Indian Dataset Seeder")
    print("=" * 60)

    # ── Connect to Supabase ──
    print(f"\n🔌 Connecting to Supabase...")
    print(f"   URL: {settings.supabase_url}")

    try:
        supabase: Client = create_client(settings.supabase_url, settings.supabase_key)
        # Quick health check
        supabase.table("customers").select("id").limit(1).execute()
        print("   ✅ Connected successfully.")
    except Exception as e:
        print(f"\n❌ FATAL: Cannot connect to Supabase!")
        print(f"   Error: {e}")
        print(f"   Check your SUPABASE_URL and SUPABASE_KEY in .env")
        sys.exit(1)

    # ── Generate dataset ──
    print("\n📊 Generating Indian synthetic dataset (seed=42)...")
    data = generate_full_dataset()
    print(f"   Products:  {len(data['products'])}")
    print(f"   Customers: {len(data['customers'])}")
    print(f"   Orders:    {len(data['orders'])}")
    print(f"   Tickets:   {len(data['tickets'])}")

    try:
        # ── 1. Seed Products ──
        print("\n📦 Step 1/4 — Seeding products...")
        product_id_map = {}  # index → actual DB id

        for idx, p in enumerate(data["products"]):
            product_data = {
                "name": p["name"],
                "category": p["category"],
                "price": p["price"],
                "stock": p["stock"],
                "description": p["description"],
            }

            # Check if product already exists (by name)
            existing = supabase.table("products").select("id").eq("name", p["name"]).execute()
            if existing.data:
                product_id_map[idx] = existing.data[0]["id"]
                print(f"   ⏭️  {p['name']} already exists (ID: {existing.data[0]['id']})")
            else:
                res = supabase.table("products").insert(product_data).execute()
                if not res.data:
                    raise RuntimeError(f"Failed to insert product: {p['name']}")
                product_id_map[idx] = res.data[0]["id"]
                print(f"   ✅ {p['name']} → ID: {res.data[0]['id']}")

        print(f"   📦 {len(product_id_map)} products ready.")

        # ── 2. Seed Customers ──
        print("\n👤 Step 2/4 — Seeding customers...")
        customer_id_map = {}  # index → actual DB id

        for idx, c in enumerate(data["customers"]):
            customer_data = {
                "name": c["name"],
                "email": c["email"],
                "age": c["age"],
                "gender": c["gender"],
            }

            # Check if customer already exists (by email)
            existing = supabase.table("customers").select("id").eq("email", c["email"]).execute()
            if existing.data:
                customer_id_map[idx] = existing.data[0]["id"]
            else:
                res = supabase.table("customers").insert(customer_data).execute()
                if not res.data:
                    raise RuntimeError(f"Failed to insert customer: {c['name']} ({c['email']})")
                customer_id_map[idx] = res.data[0]["id"]

        print(f"   ✅ {len(customer_id_map)} customers seeded.")
        # Print a few sample names
        for i in range(min(3, len(data["customers"]))):
            c = data["customers"][i]
            print(f"      → {c['name']} ({c['city']}, {c['state']}) — ID: {customer_id_map[i]}")

        # ── 3. Seed Orders ──
        print("\n🛒 Step 3/4 — Seeding orders...")
        order_id_map = {}  # order list index → actual DB id
        order_customer_map = {}  # order list index → customer_id

        # Fetch existing orders to prevent duplicates
        orders_to_insert = []
        existing_orders_res = supabase.table("orders").select("id, tracking_number, customer_id, product_id").execute()
        existing_orders = {}
        for row in existing_orders_res.data:
            key = f"{row['customer_id']}_{row['product_id']}_{row.get('tracking_number', '')}"
            existing_orders[key] = row["id"]

        for idx, o in enumerate(data["orders"]):
            cust_id = customer_id_map[o["customer_index"]]
            prod_id = product_id_map[o["product_index"]]
            key = f"{cust_id}_{prod_id}_{o.get('tracking_number', '')}"
            
            if key in existing_orders:
                order_id_map[idx] = existing_orders[key]
                order_customer_map[idx] = cust_id
            else:
                order_date = (datetime.now(timezone.utc) - timedelta(days=o["days_ago"])).isoformat()
                orders_to_insert.append({
                    "_idx": idx, # temporary for mapping back
                    "customer_id": cust_id,
                    "product_id": prod_id,
                    "order_date": order_date,
                    "status": o["status"],
                    "tracking_number": o["tracking_number"],
                })
                order_customer_map[idx] = cust_id

        # Insert orders in batches of 50
        inserted_orders = []
        for i in range(0, len(orders_to_insert), 50):
            batch = [{k: v for k, v in item.items() if k != "_idx"} for item in orders_to_insert[i:i + 50]]
            res = supabase.table("orders").insert(batch).execute()
            if not res.data:
                raise RuntimeError(f"Failed to insert orders batch starting at index {i}")
            
            # Map inserted IDs back
            for j, inserted_row in enumerate(res.data):
                original_idx = orders_to_insert[i + j]["_idx"]
                order_id_map[original_idx] = inserted_row["id"]

        print(f"   ✅ {len(orders_to_insert)} new orders seeded. (Total mapped: {len(order_id_map)})")

        # Print order status distribution
        status_counts = {}
        for o in data["orders"]:
            status_counts[o["status"]] = status_counts.get(o["status"], 0) + 1
        for status, count in sorted(status_counts.items()):
            print(f"      → {status}: {count}")

        # ── 4. Seed Tickets ──
        print("\n🎫 Step 4/4 — Seeding support tickets...")
        # Fetch existing tickets to prevent duplicates
        tickets_to_insert = []
        existing_tickets_res = supabase.table("tickets").select("id, customer_id, order_id, subject").execute()
        existing_tickets = {}
        for row in existing_tickets_res.data:
            key = f"{row['customer_id']}_{row['order_id']}_{row['subject']}"
            existing_tickets[key] = row["id"]

        for t in data["tickets"]:
            order_idx = t["order_index"]
            order_id = order_id_map[order_idx]
            cust_id = order_customer_map[order_idx]
            key = f"{cust_id}_{order_id}_{t['subject']}"
            
            if key not in existing_tickets:
                ticket_data = {
                    "customer_id": cust_id,
                    "order_id": order_id,
                    "subject": t["subject"],
                    "description": t["description"],
                    "type": t["type"],
                    "status": t["status"],
                    "priority": t["priority"],
                    "channel": t["channel"],
                    "assigned_agent": t["assigned_agent"],
                    "resolution": t["resolution"],
                    "satisfaction_rating": t["satisfaction_rating"],
                }
                tickets_to_insert.append(ticket_data)

        # Insert tickets in batches of 50
        inserted_tickets = []
        for i in range(0, len(tickets_to_insert), 50):
            batch = tickets_to_insert[i:i + 50]
            res = supabase.table("tickets").insert(batch).execute()
            if not res.data:
                raise RuntimeError(f"Failed to insert tickets batch starting at index {i}")
            inserted_tickets.extend(res.data)

        print(f"   ✅ {len(tickets_to_insert)} new tickets seeded.")

        # Print ticket type distribution
        type_counts = {}
        for t in data["tickets"]:
            type_counts[t["type"]] = type_counts.get(t["type"], 0) + 1
        for ttype, count in sorted(type_counts.items()):
            print(f"      → {ttype}: {count}")

        # ── Summary ──
        print("\n" + "=" * 60)
        print("🎉 SUPABASE SEEDING COMPLETE!")
        print(f"   Products:  {len(product_id_map)}")
        print(f"   Customers: {len(customer_id_map)}")
        print(f"   Orders:    {len(order_id_map)}")
        print(f"   Tickets:   {len(inserted_tickets)}")
        print("=" * 60)

        # Print useful info for testing
        print("\n📝 Sample data for testing:")
        first_customer = data["customers"][0]
        first_cust_id = customer_id_map[0]
        print(f"   Customer: {first_customer['name']} (ID: {first_cust_id}, Email: {first_customer['email']})")

        # Find an order for the first customer
        for idx, o in enumerate(data["orders"]):
            if o["customer_index"] == 0:
                oid = order_id_map[idx]
                prod = data["products"][o["product_index"]]
                print(f"   Order:    #{oid} — {prod['name']} (₹{prod['price']:,.2f}) — Status: {o['status']}")
                break

    except Exception as e:
        print(f"\n❌ Error during seeding: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    seed()
