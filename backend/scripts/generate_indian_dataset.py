"""Generate deterministic Indian e-commerce customer-support seed data.

The generated records are synthetic and contain no real customer information.
They are intentionally aligned with the Supabase schema used by CSA.
"""

from __future__ import annotations

import csv
import random
from datetime import date, timedelta
from pathlib import Path

FIRST_NAMES = [
    "Aarav", "Aditi", "Aditya", "Akash", "Ananya", "Arjun", "Diya", "Isha",
    "Kabir", "Kavya", "Karan", "Meera", "Neha", "Nikhil", "Pooja", "Rahul",
    "Riya", "Rohan", "Sakshi", "Shreya", "Siddharth", "Simran", "Tanvi", "Varun",
    "Vikram", "Yash", "Zoya", "Ishaan", "Priya", "Manish",
]
LAST_NAMES = [
    "Sharma", "Jain", "Patel", "Verma", "Reddy", "Iyer", "Nair", "Mehta",
    "Kapoor", "Gupta", "Singh", "Khan", "Joshi", "Bansal", "Malhotra", "Das",
    "Kulkarni", "Desai", "Menon", "Chopra",
]
CITIES = [
    ("Bengaluru", "Karnataka", "560001"), ("Mumbai", "Maharashtra", "400001"),
    ("Delhi", "Delhi", "110001"), ("Hyderabad", "Telangana", "500001"),
    ("Pune", "Maharashtra", "411001"), ("Chennai", "Tamil Nadu", "600001"),
    ("Kolkata", "West Bengal", "700001"), ("Ahmedabad", "Gujarat", "380001"),
    ("Jaipur", "Rajasthan", "302001"), ("Kochi", "Kerala", "682001"),
    ("Lucknow", "Uttar Pradesh", "226001"), ("Chandigarh", "Chandigarh", "160017"),
]

PRODUCTS = [
    ("NovaPhone X1", "Smartphones", 44999, 85, "5G smartphone with 6.6-inch AMOLED display and 256GB storage."),
    ("NovaPhone Lite", "Smartphones", 24999, 140, "Budget 5G smartphone with 6.5-inch display and 128GB storage."),
    ("PixelView Pro 13", "Laptops", 74999, 55, "13.3-inch laptop with 16GB RAM and 512GB SSD."),
    ("WorkMate 15", "Laptops", 58999, 75, "15.6-inch productivity laptop with 16GB RAM and 512GB SSD."),
    ("TabMax 11", "Tablets", 32999, 90, "11-inch tablet with 256GB storage and stylus support."),
    ("SoundBeat ANC", "Audio", 7999, 180, "Wireless over-ear headphones with active noise cancellation."),
    ("SoundBeat Buds", "Audio", 2999, 250, "True wireless earbuds with low-latency mode."),
    ("GameBox X", "Gaming", 49999, 35, "4K gaming console with 1TB SSD."),
    ("Pro Controller", "Gaming Accessories", 5999, 120, "Wireless controller with haptic feedback."),
    ("FitTrack Pro", "Wearables", 6999, 160, "Fitness band with GPS, SpO2 and sleep tracking."),
    ("SmartWatch Neo", "Wearables", 12999, 100, "AMOLED smartwatch with calling and health tracking."),
    ("HomeHub Mini", "Smart Home", 4999, 130, "Compact smart-home hub with voice control."),
    ("SecureCam 2K", "Smart Home", 3499, 145, "2K indoor security camera with night vision."),
    ("AirPure Plus", "Home Appliances", 11999, 70, "HEPA air purifier for bedrooms and living rooms."),
    ("CleanBot S1", "Home Appliances", 24999, 45, "Robot vacuum with mopping and room mapping."),
    ("PowerBank 20K", "Accessories", 1999, 300, "20000mAh fast-charging power bank."),
    ("USB-C Hub 8-in-1", "Accessories", 2499, 220, "8-in-1 USB-C hub with HDMI and Ethernet."),
    ("FastCharge 65W", "Accessories", 1799, 260, "65W USB-C GaN charger for phones and laptops."),
]

ISSUES = [
    ("delivery", "Where is my order?", "My order has not arrived yet and I want to know the latest delivery status."),
    ("delivery", "Delivery delayed", "The delivery date has passed but my package has still not been delivered."),
    ("payment", "UPI payment issue", "I paid using UPI but the order is still showing as payment pending."),
    ("payment", "Payment deducted twice", "The amount appears to have been debited twice for the same order."),
    ("refund", "Refund not received", "My refund was approved but I have not received the amount in my bank account yet."),
    ("refund", "Refund timeline", "Please tell me how long a refund normally takes after cancellation."),
    ("cancellation", "Cancel my order", "I placed the order recently and would like to cancel it."),
    ("billing", "GST invoice needed", "I need a GST invoice for my purchase for reimbursement."),
    ("billing", "Invoice details incorrect", "The billing details on my invoice are incorrect and need to be changed."),
    ("technical_issue", "App checkout error", "The checkout page is showing an error when I try to place the order."),
    ("technical_issue", "OTP not received", "I am not receiving the OTP needed to complete my login or payment."),
    ("inquiry", "Product availability", "Is this product currently available for delivery to my PIN code?"),
    ("inquiry", "Product warranty", "Please share the warranty coverage and duration for this product."),
    ("inquiry", "COD availability", "Is cash on delivery available for my location?"),
    ("complaint", "Damaged product", "The product arrived damaged and I need help with a replacement."),
]

CHANNELS = ["chat", "email", "phone", "social_media"]
STATUSES = ["open", "pending_customer", "closed"]
PRIORITIES = ["low", "medium", "high", "critical"]
AGENTS = ["Priya", "Arjun", "Neha", "Rahul", "Ananya"]


def build_dataset(customer_count: int = 150, ticket_count: int = 600, seed: int = 42):
    """Return deterministic customers, products, orders and tickets."""
    rng = random.Random(seed)
    start = date(2025, 1, 1)
    end = date(2026, 9, 1)

    customers = []
    used_emails = set()
    for i in range(customer_count):
        first = FIRST_NAMES[i % len(FIRST_NAMES)]
        last = LAST_NAMES[(i * 7) % len(LAST_NAMES)]
        city, state, pin = CITIES[i % len(CITIES)]
        email = f"{first.lower()}.{last.lower()}{i + 1}@example.in"
        used_emails.add(email)
        customers.append({
            "name": f"{first} {last}",
            "email": email,
            "age": rng.randint(19, 58),
            "gender": rng.choice(["Male", "Female"]),
            "city": city,
            "state": state,
            "pin_code": pin,
        })

    orders = []
    tickets = []
    for ticket_id in range(1, ticket_count + 1):
        customer_id = rng.randint(1, customer_count)
        product_id = rng.randint(1, len(PRODUCTS))
        issue_type, subject, description = rng.choice(ISSUES)
        purchase_date = start + timedelta(days=rng.randint(0, max(1, (end - start).days)))
        order_status = rng.choice(["active", "shipped", "delivered"])
        tracking = f"IN{rng.randint(100000000, 999999999)}"
        orders.append({
            "customer_id": customer_id,
            "product_id": product_id,
            "order_date": purchase_date.isoformat(),
            "status": order_status,
            "tracking_number": tracking,
        })

        city = customers[customer_id - 1]["city"]
        state = customers[customer_id - 1]["state"]
        indian_context = f"Customer is in {city}, {state}."
        if rng.random() < 0.30:
            description = description + " Customer prefers a quick update over chat."
        tickets.append({
            "customer_id": customer_id,
            "order_index": ticket_id - 1,
            "subject": subject,
            "description": f"{description} {indian_context}",
            "type": issue_type,
            "status": rng.choice(STATUSES),
            "priority": rng.choice(PRIORITIES),
            "channel": rng.choice(CHANNELS),
            "assigned_agent": rng.choice(AGENTS),
            "resolution": None,
            "satisfaction_rating": rng.choice([None, None, 3, 4, 5]),
        })

    return customers, orders, tickets


def write_ticket_csv(path: Path, ticket_count: int = 600, seed: int = 42) -> None:
    """Write a human-readable synthetic ticket dataset for inspection/reuse."""
    customers, orders, tickets = build_dataset(ticket_count=ticket_count, seed=seed)
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "Customer Name", "Customer Email", "Customer Age", "Customer Gender",
        "City", "State", "PIN Code", "Order ID", "Product", "Order Date",
        "Order Status", "Tracking Number", "Ticket Subject", "Ticket Description",
        "Ticket Type", "Ticket Status", "Ticket Priority", "Ticket Channel",
        "Assigned Agent", "Resolution", "Customer Satisfaction Rating",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for index, ticket in enumerate(tickets):
            customer = customers[ticket["customer_id"] - 1]
            order = orders[index]
            product = PRODUCTS[order["product_id"] - 1]
            writer.writerow({
                "Customer Name": customer["name"],
                "Customer Email": customer["email"],
                "Customer Age": customer["age"],
                "Customer Gender": customer["gender"],
                "City": customer["city"],
                "State": customer["state"],
                "PIN Code": customer["pin_code"],
                "Order ID": index + 1,
                "Product": product[0],
                "Order Date": order["order_date"],
                "Order Status": order["status"],
                "Tracking Number": order["tracking_number"],
                "Ticket Subject": ticket["subject"],
                "Ticket Description": ticket["description"],
                "Ticket Type": ticket["type"],
                "Ticket Status": ticket["status"],
                "Ticket Priority": ticket["priority"],
                "Ticket Channel": ticket["channel"],
                "Assigned Agent": ticket["assigned_agent"],
                "Resolution": ticket["resolution"] or "",
                "Customer Satisfaction Rating": ticket["satisfaction_rating"] or "",
            })


if __name__ == "__main__":
    output = Path(__file__).resolve().parents[2] / "dataset" / "indian_customer_support_tickets.csv"
    write_ticket_csv(output)
    print(f"Generated deterministic Indian dataset: {output}")
