"""
Seed the Supabase database with data from the customer_support_tickets.csv file.
Creates customers, products, orders, and tickets using the Supabase REST API.

Usage:
    cd backend
    python -m scripts.seed_database
"""

import sys
import os
import random
from datetime import datetime, timedelta

import pandas as pd
from supabase import create_client, Client

# Add parent dir to path so we can import app modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.config import settings

# ──────────────────────────────────────────────
#  Product catalog (matches knowledge_base/product_catalog.md)
# ──────────────────────────────────────────────
PRODUCTS = [
    {"name": "ProPhone X14", "category": "Smartphones", "price": 899.99, "stock": 150, "description": "Flagship smartphone with 6.7\" AMOLED display, 128GB storage, 48MP triple camera system."},
    {"name": "ProPhone X14 Lite", "category": "Smartphones", "price": 599.99, "stock": 200, "description": "Mid-range smartphone with 6.4\" LCD display, 64GB storage."},
    {"name": "ProTab 12", "category": "Tablets", "price": 499.99, "stock": 100, "description": "12.4\" tablet with stylus support, 256GB storage."},
    {"name": "UltraBook Pro 15", "category": "Laptops", "price": 1299.99, "stock": 80, "description": "15.6\" IPS display, Intel i7 processor, 16GB RAM, 512GB SSD."},
    {"name": "UltraBook Air 13", "category": "Laptops", "price": 899.99, "stock": 120, "description": "Ultra-thin 13.3\" laptop, Intel i5, 8GB RAM, 256GB SSD."},
    {"name": "WorkStation Z9", "category": "Desktops", "price": 2499.99, "stock": 30, "description": "High-performance desktop with Intel Xeon, 64GB ECC RAM."},
    {"name": "SmartHub 3.0", "category": "Smart Home", "price": 129.99, "stock": 250, "description": "Central smart home hub with voice assistant."},
    {"name": "SmartCam Pro", "category": "Smart Home", "price": 79.99, "stock": 300, "description": "1080p indoor/outdoor security camera with night vision."},
    {"name": "SmartThermo E2", "category": "Smart Home", "price": 199.99, "stock": 180, "description": "Learning thermostat that adapts to your schedule."},
    {"name": "SoundWave Pro Headphones", "category": "Audio", "price": 249.99, "stock": 200, "description": "Over-ear wireless headphones with ANC, 40-hour battery."},
    {"name": "SoundBar X500", "category": "Audio", "price": 349.99, "stock": 90, "description": "5.1 channel soundbar with wireless subwoofer."},
    {"name": "GameConsole Elite", "category": "Gaming", "price": 499.99, "stock": 60, "description": "Next-gen gaming console with 4K/120fps support, 1TB SSD."},
    {"name": "ProController V2", "category": "Gaming Accessories", "price": 69.99, "stock": 400, "description": "Wireless gaming controller with haptic feedback."},
    {"name": "FitBand Ultra", "category": "Wearables", "price": 149.99, "stock": 350, "description": "Advanced fitness tracker with heart rate, SpO2, GPS."},
    {"name": "SmartWatch Series 5", "category": "Wearables", "price": 399.99, "stock": 140, "description": "Premium smartwatch with AMOLED display, ECG."},
    {"name": "RoboClean X1", "category": "Home Appliances", "price": 549.99, "stock": 70, "description": "Robot vacuum and mop combo with LiDAR navigation."},
    {"name": "AirPure 360", "category": "Home Appliances", "price": 299.99, "stock": 110, "description": "HEPA air purifier for rooms up to 1000 sq ft."},
]

TICKET_TYPE_MAP = {
    "Technical issue": "technical_issue",
    "Billing inquiry": "billing",
    "Refund request": "refund",
    "Cancellation request": "cancellation",
    "Product inquiry": "inquiry",
}

TICKET_STATUS_MAP = {
    "Open": "open",
    "Pending Customer Response": "pending_customer",
    "Closed": "closed",
}

PRIORITY_MAP = {
    "Low": "low",
    "Medium": "medium",
    "High": "high",
    "Critical": "critical",
}

CHANNEL_MAP = {
    "Email": "email",
    "Phone": "phone",
    "Chat": "chat",
    "Social media": "social_media",
}

AGENTS = ["Agent_Alpha", "Agent_Beta", "Agent_Gamma", "Agent_Delta", "Agent_Echo"]


def seed():
    """Main seeding function."""
    print("🔌 Connecting to Supabase...")
    supabase: Client = create_client(settings.supabase_url, settings.supabase_key)

    # Load the CSV
    csv_path = os.path.join(os.path.dirname(__file__), "..", "..", "dataset", "customer_support_tickets.csv")
    if not os.path.exists(csv_path):
        print(f"❌ CSV not found at {csv_path}")
        return

    df = pd.read_csv(csv_path)
    # We will only use the first 500 rows for faster API seeding
    df = df.head(500)
    print(f"📊 Loaded {len(df)} rows from customer_support_tickets.csv")

    try:
        # ── 1. Seed Products ──
        print("📦 Seeding products...")
        product_objects = {}
        # Clear existing first
        supabase.table("products").delete().neq("id", 0).execute()
        
        for p in PRODUCTS:
            res = supabase.table("products").insert(p).execute()
            prod_id = res.data[0]["id"]
            product_objects[p["name"]] = prod_id
            
        print(f"   ✅ {len(PRODUCTS)} products seeded.")

        # ── 2. Seed Customers ──
        print("👤 Seeding customers...")
        # Clear existing first
        supabase.table("customers").delete().neq("id", 0).execute()
        
        customer_map = {}  # email -> id
        customers_to_insert = []
        for _, row in df.iterrows():
            email = row["Customer Email"]
            if email not in customer_map:
                customers_to_insert.append({
                    "name": row["Customer Name"],
                    "email": email,
                    "age": int(row["Customer Age"]) if pd.notna(row["Customer Age"]) else None,
                    "gender": row["Customer Gender"] if pd.notna(row["Customer Gender"]) else None,
                })
                customer_map[email] = True # Mark as seen

        # Insert in batches of 100
        print(f"   Inserting {len(customers_to_insert)} unique customers...")
        inserted_customers = []
        for i in range(0, len(customers_to_insert), 100):
            batch = customers_to_insert[i:i+100]
            res = supabase.table("customers").insert(batch).execute()
            inserted_customers.extend(res.data)
            
        # Re-build map with actual IDs
        customer_map = {c["email"]: c["id"] for c in inserted_customers}
        print(f"   ✅ {len(customer_map)} unique customers seeded.")

        # ── 3. Seed Orders & Tickets ──
        print("🎫 Seeding orders and tickets...")
        # Clear existing first
        supabase.table("tickets").delete().neq("id", 0).execute()
        supabase.table("orders").delete().neq("id", 0).execute()
        
        product_names = list(product_objects.keys())
        orders_to_insert = []
        tickets_data = [] # We need to hold ticket rows until orders are inserted to map IDs

        # Prepare orders
        for idx, row in df.iterrows():
            cust_id = customer_map[row["Customer Email"]]
            prod_name = random.choice(product_names)
            prod_id = product_objects[prod_name]

            purchase_date = None
            if pd.notna(row.get("Date of Purchase")):
                try:
                    purchase_date = datetime.strptime(str(row["Date of Purchase"]), "%Y-%m-%d").isoformat()
                except ValueError:
                    purchase_date = (datetime.now() - timedelta(days=random.randint(30, 365))).isoformat()

            orders_to_insert.append({
                "customer_id": cust_id,
                "product_id": prod_id,
                "order_date": purchase_date,
                "status": random.choice(["active", "shipped", "delivered"]),
                "tracking_number": f"TRK{random.randint(100000, 999999)}",
            })
            tickets_data.append((idx, row, cust_id))

        # Insert orders in batches
        inserted_orders = []
        for i in range(0, len(orders_to_insert), 100):
            batch = orders_to_insert[i:i+100]
            res = supabase.table("orders").insert(batch).execute()
            inserted_orders.extend(res.data)

        # Prepare tickets (using the newly created order IDs)
        tickets_to_insert = []
        for i, (idx, row, cust_id) in enumerate(tickets_data):
            order_id = inserted_orders[i]["id"]
            
            ticket_type = TICKET_TYPE_MAP.get(row.get("Ticket Type", ""), "inquiry")
            ticket_status = TICKET_STATUS_MAP.get(row.get("Ticket Status", ""), "open")
            priority = PRIORITY_MAP.get(row.get("Ticket Priority", ""), "medium")
            channel = CHANNEL_MAP.get(row.get("Ticket Channel", ""), "chat")

            tickets_to_insert.append({
                "customer_id": cust_id,
                "order_id": order_id,
                "subject": row.get("Ticket Subject", "General Inquiry"),
                "description": str(row.get("Ticket Description", ""))[:500],
                "type": ticket_type,
                "status": ticket_status,
                "priority": priority,
                "channel": channel,
                "assigned_agent": random.choice(AGENTS),
                "resolution": str(row.get("Resolution", "")) if pd.notna(row.get("Resolution")) else None,
                "satisfaction_rating": int(row["Customer Satisfaction Rating"]) if pd.notna(row.get("Customer Satisfaction Rating")) else None,
            })

        # Insert tickets in batches
        for i in range(0, len(tickets_to_insert), 100):
            batch = tickets_to_insert[i:i+100]
            supabase.table("tickets").insert(batch).execute()

        print(f"   ✅ {len(orders_to_insert)} orders seeded.")
        print(f"   ✅ {len(tickets_to_insert)} tickets seeded.")

        # ── Summary ──
        print("\n" + "=" * 50)
        print("🎉 SUPABASE SEEDING COMPLETE!")
        print(f"   Products:  {len(PRODUCTS)}")
        print(f"   Customers: {len(customer_map)}")
        print(f"   Orders:    {len(orders_to_insert)}")
        print(f"   Tickets:   {len(tickets_to_insert)}")
        print("=" * 50)

    except Exception as e:
        print(f"❌ Error during seeding: {e}")
        raise


if __name__ == "__main__":
    seed()
