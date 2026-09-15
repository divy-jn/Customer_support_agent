"""Seed Supabase with deterministic synthetic Indian e-commerce support data.

Usage:
    cd backend
    python -m scripts.seed_database

The data is generated locally by generate_indian_dataset.py; no real customer
information is used. Existing demo data in the five CSA tables is replaced.
"""

from __future__ import annotations

import sys
from pathlib import Path

from supabase import Client, create_client

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings
from scripts.generate_indian_dataset import PRODUCTS, build_dataset


BATCH_SIZE = 100


def chunks(items, size=BATCH_SIZE):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def seed(customer_count: int = 150, ticket_count: int = 600, seed: int = 42):
    print("🔌 Connecting to Supabase...")
    supabase: Client = create_client(settings.supabase_url, settings.supabase_key)

    customers, orders, tickets = build_dataset(
        customer_count=customer_count,
        ticket_count=ticket_count,
        seed=seed,
    )
    print(f"📊 Generated {len(customers)} customers, {len(orders)} orders and {len(tickets)} tickets")

    try:
        # Child tables first because of foreign keys.
        print("🧹 Clearing old demo data...")
        for table in ("conversations", "tickets", "orders", "products", "customers"):
            # id > 0 matches every normal identity row without assuming the current max id.
            supabase.table(table).delete().gt("id", 0).execute()

        print("📦 Seeding products...")
        product_rows = [
            {
                "name": name,
                "category": category,
                "price": price,
                "stock": stock,
                "description": description,
            }
            for name, category, price, stock, description in PRODUCTS
        ]
        product_response = supabase.table("products").insert(product_rows).execute()
        product_map = {row["name"]: row["id"] for row in product_response.data}
        print(f"   ✅ {len(product_map)} products")

        print("👤 Seeding customers...")
        customer_rows = [
            {
                "name": customer["name"],
                "email": customer["email"],
                "age": customer["age"],
                "gender": customer["gender"],
            }
            for customer in customers
        ]
        inserted_customers = []
        for batch in chunks(customer_rows):
            inserted_customers.extend(
                supabase.table("customers").insert(batch).execute().data
            )
        customer_map = {row["email"]: row["id"] for row in inserted_customers}
        print(f"   ✅ {len(customer_map)} customers")

        print("🛒 Seeding orders...")
        order_rows = []
        for order in orders:
            # order customer_id/product_id are deterministic 1-based indexes into
            # the generated lists, not database primary keys.
            customer = customers[order["customer_id"] - 1]
            product = PRODUCTS[order["product_id"] - 1]
            order_rows.append(
                {
                    "customer_id": customer_map[customer["email"]],
                    "product_id": product_map[product[0]],
                    "order_date": order["order_date"],
                    "status": order["status"],
                    "tracking_number": order["tracking_number"],
                }
            )

        inserted_orders = []
        for batch in chunks(order_rows):
            inserted_orders.extend(
                supabase.table("orders").insert(batch).execute().data
            )
        print(f"   ✅ {len(inserted_orders)} orders")

        print("🎫 Seeding tickets...")
        ticket_rows = []
        for index, ticket in enumerate(tickets):
            customer = customers[ticket["customer_id"] - 1]
            ticket_rows.append(
                {
                    "customer_id": customer_map[customer["email"]],
                    "order_id": inserted_orders[index]["id"],
                    "subject": ticket["subject"],
                    "description": ticket["description"],
                    "type": ticket["type"],
                    "status": ticket["status"],
                    "priority": ticket["priority"],
                    "channel": ticket["channel"],
                    "assigned_agent": ticket["assigned_agent"],
                    "resolution": ticket["resolution"],
                    "satisfaction_rating": ticket["satisfaction_rating"],
                }
            )

        for batch in chunks(ticket_rows):
            supabase.table("tickets").insert(batch).execute()
        print(f"   ✅ {len(ticket_rows)} tickets")

        print("\n" + "=" * 56)
        print("🎉 INDIAN CSA DEMO DATA SEEDED SUCCESSFULLY")
        print(f"   Products:  {len(product_map)}")
        print(f"   Customers: {len(customer_map)}")
        print(f"   Orders:    {len(inserted_orders)}")
        print(f"   Tickets:   {len(ticket_rows)}")
        print("   Context:   India | INR | UPI | COD | GST | PIN codes")
        print("   Seed:      deterministic (42)")
        print("=" * 56)

    except Exception as exc:
        print(f"❌ Seeding failed: {exc}")
        raise


if __name__ == "__main__":
    seed()
