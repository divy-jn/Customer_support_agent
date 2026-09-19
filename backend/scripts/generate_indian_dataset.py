"""
Indian Synthetic Dataset Generator for Customer Support Agent.

Generates deterministic, realistic Indian e-commerce data:
  - 30 customers with Indian names, cities, states, PIN codes
  - 25 products with INR pricing
  - 60 orders with proper customer→product relationships
  - 40 support tickets with Indian-context scenarios

All data is fictional. No real PII is used.

Usage:
    cd backend
    python -m scripts.generate_indian_dataset
"""

import random

# Fixed seed for deterministic, reproducible output
random.seed(42)


# ──────────────────────────────────────────────
#  Indian Cities / States / PIN Codes
# ──────────────────────────────────────────────
INDIAN_LOCATIONS = [
    {"city": "Bengaluru", "state": "Karnataka", "pin": "560001"},
    {"city": "Mumbai", "state": "Maharashtra", "pin": "400001"},
    {"city": "Delhi", "state": "Delhi", "pin": "110001"},
    {"city": "Hyderabad", "state": "Telangana", "pin": "500001"},
    {"city": "Pune", "state": "Maharashtra", "pin": "411001"},
    {"city": "Chennai", "state": "Tamil Nadu", "pin": "600001"},
    {"city": "Kolkata", "state": "West Bengal", "pin": "700001"},
    {"city": "Ahmedabad", "state": "Gujarat", "pin": "380001"},
    {"city": "Jaipur", "state": "Rajasthan", "pin": "302001"},
    {"city": "Kochi", "state": "Kerala", "pin": "682001"},
    {"city": "Lucknow", "state": "Uttar Pradesh", "pin": "226001"},
    {"city": "Chandigarh", "state": "Chandigarh", "pin": "160001"},
    {"city": "Indore", "state": "Madhya Pradesh", "pin": "452001"},
    {"city": "Noida", "state": "Uttar Pradesh", "pin": "201301"},
    {"city": "Gurgaon", "state": "Haryana", "pin": "122001"},
]

# Indian first names (male and female)
MALE_FIRST_NAMES = [
    "Aarav", "Vivaan", "Aditya", "Rohan", "Arjun",
    "Karan", "Ishaan", "Siddharth", "Ravi", "Deepak",
    "Rajesh", "Vikram", "Amit", "Prashant", "Nikhil",
]
FEMALE_FIRST_NAMES = [
    "Aanya", "Priya", "Sneha", "Ananya", "Kavya",
    "Meera", "Pooja", "Riya", "Nisha", "Simran",
    "Divya", "Shreya", "Neha", "Swati", "Anjali",
]
LAST_NAMES = [
    "Sharma", "Patel", "Singh", "Kumar", "Reddy",
    "Gupta", "Joshi", "Nair", "Verma", "Iyer",
    "Mehta", "Chatterjee", "Das", "Rao", "Malhotra",
]


# ──────────────────────────────────────────────
#  Products — Indian Market (INR)
# ──────────────────────────────────────────────
PRODUCTS = [
    # Smartphones
    {"name": "ProPhone X14", "category": "Smartphones", "price": 74999.00, "stock": 150,
     "description": "Flagship smartphone with 6.7\" AMOLED display, 128GB storage, 48MP triple camera system. 1-year manufacturer warranty."},
    {"name": "ProPhone X14 Lite", "category": "Smartphones", "price": 42999.00, "stock": 200,
     "description": "Mid-range smartphone with 6.4\" LCD display, 64GB storage, 13MP camera."},
    {"name": "BudgetPhone A5", "category": "Smartphones", "price": 12999.00, "stock": 400,
     "description": "Affordable smartphone with 6.5\" HD+ display, 32GB storage, 5000mAh battery."},
    # Laptops
    {"name": "UltraBook Pro 15", "category": "Laptops", "price": 89999.00, "stock": 80,
     "description": "15.6\" IPS display, Intel i7 processor, 16GB RAM, 512GB SSD. Ideal for professionals."},
    {"name": "UltraBook Air 13", "category": "Laptops", "price": 64999.00, "stock": 120,
     "description": "Ultra-thin 13.3\" laptop, Intel i5, 8GB RAM, 256GB SSD. Perfect for students."},
    {"name": "GamerBook X17", "category": "Laptops", "price": 124999.00, "stock": 40,
     "description": "17\" FHD 144Hz gaming laptop, RTX 4060, 16GB RAM, 1TB SSD, RGB keyboard."},
    # Tablets
    {"name": "ProTab 12", "category": "Tablets", "price": 39999.00, "stock": 100,
     "description": "12.4\" tablet with stylus support, 256GB storage. Great for note-taking and media."},
    # Audio
    {"name": "SoundWave Pro Headphones", "category": "Headphones", "price": 19999.00, "stock": 200,
     "description": "Over-ear wireless headphones with ANC, 40-hour battery life, Hi-Res Audio certified."},
    {"name": "BassBuds TWS", "category": "Earbuds", "price": 3499.00, "stock": 500,
     "description": "True wireless earbuds with 28-hour battery, IPX5 water resistance, low-latency gaming mode."},
    {"name": "BassBuds Pro ANC", "category": "Earbuds", "price": 7999.00, "stock": 300,
     "description": "Premium TWS earbuds with active noise cancellation, transparency mode, wireless charging case."},
    {"name": "SoundBar X500", "category": "Home Audio", "price": 24999.00, "stock": 90,
     "description": "5.1 channel soundbar with wireless subwoofer, Dolby Atmos support."},
    # Gaming
    {"name": "GameConsole Elite", "category": "Gaming", "price": 49999.00, "stock": 60,
     "description": "Next-gen gaming console with 4K/120fps support, 1TB SSD, 2 wireless controllers included."},
    {"name": "ProController V2", "category": "Gaming Accessories", "price": 5499.00, "stock": 400,
     "description": "Wireless gaming controller with haptic feedback, adaptive triggers."},
    # Wearables
    {"name": "SmartWatch Series 5", "category": "Smart Watches", "price": 29999.00, "stock": 140,
     "description": "Premium smartwatch with AMOLED display, ECG, SpO2 monitoring, 3-day battery."},
    {"name": "FitBand Ultra", "category": "Smart Watches", "price": 8999.00, "stock": 350,
     "description": "Advanced fitness tracker with heart rate, SpO2, GPS, 14-day battery life."},
    # Home Appliances
    {"name": "RoboClean X1", "category": "Home Appliances", "price": 34999.00, "stock": 70,
     "description": "Robot vacuum and mop combo with LiDAR navigation, app control, 2-hour runtime."},
    {"name": "AirPure 360", "category": "Home Appliances", "price": 18999.00, "stock": 110,
     "description": "HEPA air purifier for rooms up to 800 sq ft, PM2.5 sensor, quiet mode."},
    {"name": "SmartCooker Pro", "category": "Home Appliances", "price": 6999.00, "stock": 200,
     "description": "Multi-function electric pressure cooker, 6L capacity, 12 preset programs."},
    # Smart Home
    {"name": "SmartHub 3.0", "category": "Smart Home", "price": 9999.00, "stock": 250,
     "description": "Central smart home hub with voice assistant, Zigbee/Wi-Fi/Bluetooth support."},
    {"name": "SmartCam Pro", "category": "Smart Home", "price": 5999.00, "stock": 300,
     "description": "1080p indoor/outdoor security camera with night vision, 2-way audio, cloud storage."},
    {"name": "SmartThermo E2", "category": "Smart Home", "price": 14999.00, "stock": 180,
     "description": "Learning thermostat that adapts to your schedule. Works with Alexa and Google Home."},
    # Accessories
    {"name": "USB-C Hub Pro 7-in-1", "category": "Accessories", "price": 2999.00, "stock": 500,
     "description": "7-in-1 USB-C hub with HDMI 4K, USB 3.0, SD card reader, PD charging."},
    {"name": "LaptopStand Ergo", "category": "Accessories", "price": 1999.00, "stock": 400,
     "description": "Aluminum adjustable laptop stand, ergonomic design, compatible with 10-17\" laptops."},
    {"name": "PowerBank 20000", "category": "Accessories", "price": 1499.00, "stock": 600,
     "description": "20000mAh power bank, 22.5W fast charge, USB-C + USB-A, LED display."},
    {"name": "Wireless Charger Pad", "category": "Accessories", "price": 1299.00, "stock": 400,
     "description": "15W Qi wireless charging pad, compatible with all Qi-enabled devices."},
]


# ──────────────────────────────────────────────
#  Support Agents
# ──────────────────────────────────────────────
SUPPORT_AGENTS = [
    "Agent Priya", "Agent Rohan", "Agent Kavya",
    "Agent Aditya", "Agent Meera",
]


# ──────────────────────────────────────────────
#  Generate Customers
# ──────────────────────────────────────────────
def generate_customers(count: int = 30) -> list[dict]:
    """Generate synthetic Indian customers."""
    # Always guarantee the demo customer exists
    customers = [{
        "name": "Amit Sharma",
        "email": "amit.sharma@example.com",
        "age": 30,
        "gender": "Male",
        "city": "Bengaluru",
        "state": "Karnataka",
        "pin_code": "560001",
    }]
    used_emails = {"amit.sharma@example.com"}

    for i in range(1, count):
        if i % 2 == 0:
            first = random.choice(MALE_FIRST_NAMES)
            gender = "Male"
        else:
            first = random.choice(FEMALE_FIRST_NAMES)
            gender = "Female"

        last = random.choice(LAST_NAMES)
        name = f"{first} {last}"
        location = random.choice(INDIAN_LOCATIONS)
        age = random.randint(20, 55)

        # Generate unique email
        email_base = f"{first.lower()}.{last.lower()}"
        email = f"{email_base}@example.in"
        suffix = 1
        while email in used_emails:
            email = f"{email_base}{suffix}@example.in"
            suffix += 1
        used_emails.add(email)

        customers.append({
            "name": name,
            "email": email,
            "age": age,
            "gender": gender,
            "city": location["city"],
            "state": location["state"],
            "pin_code": location["pin"],
        })

    return customers


# ──────────────────────────────────────────────
#  Generate Orders
# ──────────────────────────────────────────────
ORDER_STATUSES = ["active", "shipped", "delivered", "cancelled", "refunded"]
ORDER_STATUS_WEIGHTS = [0.15, 0.25, 0.40, 0.10, 0.10]


def generate_orders(customer_count: int = 30, min_orders: int = 1, max_orders: int = 4) -> list[dict]:
    """
    Generate orders. Returns list of dicts with customer_index and product_index
    (to be resolved to actual IDs after DB insertion).
    """
    orders = []
    product_count = len(PRODUCTS)

    for cust_idx in range(customer_count):
        num_orders = random.randint(min_orders, max_orders)
        used_products = set()

        for _ in range(num_orders):
            # Pick a product this customer hasn't ordered yet
            prod_idx = random.randint(0, product_count - 1)
            while prod_idx in used_products and len(used_products) < product_count:
                prod_idx = random.randint(0, product_count - 1)
            used_products.add(prod_idx)

            status = random.choices(ORDER_STATUSES, weights=ORDER_STATUS_WEIGHTS, k=1)[0]

            # Generate a realistic order date (past 6 months)
            days_ago = random.randint(1, 180)

            # Indian tracking number format
            courier_prefixes = ["DEL", "BLU", "DTC", "EKR", "XPR"]
            tracking = f"{random.choice(courier_prefixes)}{random.randint(1000000000, 9999999999)}"

            orders.append({
                "customer_index": cust_idx,
                "product_index": prod_idx,
                "days_ago": days_ago,
                "status": status,
                "tracking_number": tracking,
            })

    return orders


# ──────────────────────────────────────────────
#  Support Ticket Scenarios (Indian Context)
# ──────────────────────────────────────────────

TICKET_SCENARIOS = [
    # Delivery Issues
    {
        "subject": "Order not delivered yet",
        "description": "I placed my order 10 days ago and it still shows 'shipped'. The tracking hasn't updated in 5 days. Please help.",
        "type": "technical_issue", "priority": "high", "channel": "chat",
    },
    {
        "subject": "Delivery delayed to my area",
        "description": "My order was supposed to arrive 3 days ago but delivery partner says there is a delay due to weather. Can you provide an updated timeline?",
        "type": "technical_issue", "priority": "medium", "channel": "chat",
    },
    {
        "subject": "Wrong PIN code on delivery address",
        "description": "I entered wrong PIN code during checkout. Can you please update my delivery address before it ships?",
        "type": "technical_issue", "priority": "high", "channel": "chat",
    },
    # UPI / Payment Issues
    {
        "subject": "UPI payment deducted but order not confirmed",
        "description": "I paid via Google Pay UPI but the order page shows 'Payment Failed'. Rs 42,999 was debited from my account. Transaction ID: UPI2026091512345.",
        "type": "billing", "priority": "critical", "channel": "chat",
    },
    {
        "subject": "Double payment charged on UPI",
        "description": "I was charged twice for the same order. Both payments went through on PhonePe. Please refund the duplicate amount.",
        "type": "billing", "priority": "critical", "channel": "chat",
    },
    {
        "subject": "Payment deducted but order shows pending",
        "description": "Paid through Paytm UPI but order status still shows pending. Amount of Rs 7,999 was deducted. Please check.",
        "type": "billing", "priority": "high", "channel": "chat",
    },
    {
        "subject": "Net Banking payment failed",
        "description": "Payment through HDFC Net Banking failed but amount was debited from my account. Reference number: NEFT2026091567890.",
        "type": "billing", "priority": "high", "channel": "email",
    },
    # Cancellation / Refund
    {
        "subject": "Want to cancel my order",
        "description": "I ordered by mistake. The order was placed just 30 minutes ago and hasn't shipped yet. Please cancel it immediately.",
        "type": "cancellation", "priority": "high", "channel": "chat",
    },
    {
        "subject": "Refund not received after cancellation",
        "description": "I cancelled my order 15 days ago but haven't received my refund yet. The order was paid via credit card. When will I get my money back?",
        "type": "refund", "priority": "high", "channel": "email",
    },
    {
        "subject": "Partial refund received",
        "description": "I returned the product and was told I'd get a full refund of Rs 89,999 but only received Rs 80,000. Where is the remaining amount?",
        "type": "refund", "priority": "high", "channel": "chat",
    },
    # Product Issues
    {
        "subject": "Received damaged product",
        "description": "The laptop I received has a cracked screen. The outer box was also damaged. I need a replacement or full refund immediately.",
        "type": "technical_issue", "priority": "critical", "channel": "chat",
    },
    {
        "subject": "Wrong product delivered",
        "description": "I ordered ProPhone X14 but received ProPhone X14 Lite. Please arrange for the correct product.",
        "type": "technical_issue", "priority": "high", "channel": "chat",
    },
    {
        "subject": "Product not working after 2 days",
        "description": "My new earbuds stopped working after just 2 days. Left earbud is completely dead. This is unacceptable for a new product.",
        "type": "technical_issue", "priority": "high", "channel": "chat",
    },
    # GST / Invoice
    {
        "subject": "Need GST invoice for my order",
        "description": "I need a GST invoice for my business purchase. My GSTIN is 29ABCDE1234F1ZH. Please generate and send the invoice.",
        "type": "billing", "priority": "medium", "channel": "email",
    },
    {
        "subject": "Incorrect GST amount on invoice",
        "description": "The GST on my invoice shows 12% but for electronics it should be 18%. Please correct the invoice.",
        "type": "billing", "priority": "medium", "channel": "email",
    },
    # COD
    {
        "subject": "Is COD available for my pincode?",
        "description": "I want to order a laptop but I prefer Cash on Delivery. Is COD available for pincode 226001 (Lucknow)?",
        "type": "inquiry", "priority": "low", "channel": "chat",
    },
    {
        "subject": "COD order — delivery person not accepting card payment",
        "description": "I selected COD for my order but the delivery person says they only accept cash, not card. I don't have cash right now.",
        "type": "technical_issue", "priority": "medium", "channel": "chat",
    },
    # Warranty
    {
        "subject": "Warranty claim for smartwatch",
        "description": "My SmartWatch Series 5 screen stopped responding to touch. It's 8 months old and should be under warranty. How do I claim warranty?",
        "type": "technical_issue", "priority": "medium", "channel": "chat",
    },
    {
        "subject": "Extended warranty enquiry",
        "description": "I want to purchase extended warranty for my UltraBook Pro 15. What are the options and pricing?",
        "type": "inquiry", "priority": "low", "channel": "chat",
    },
    # Account Issues
    {
        "subject": "Cannot login - OTP not received",
        "description": "I'm trying to login but not receiving OTP on my registered mobile number +91-98765XXXXX. Tried 5 times already.",
        "type": "technical_issue", "priority": "high", "channel": "chat",
    },
    {
        "subject": "Update my phone number",
        "description": "I changed my phone number and need to update it in my account. Old number: +91-98765XXXXX, New: +91-87654XXXXX.",
        "type": "inquiry", "priority": "medium", "channel": "chat",
    },
    # Product Availability
    {
        "subject": "When will GamerBook X17 be back in stock?",
        "description": "GamerBook X17 shows out of stock. When will it be available again? I've been waiting for 2 weeks.",
        "type": "inquiry", "priority": "low", "channel": "chat",
    },
    {
        "subject": "Price drop alert",
        "description": "I want to buy the ProPhone X14 but waiting for a sale. Can you notify me when there's a price drop?",
        "type": "inquiry", "priority": "low", "channel": "chat",
    },
    # Checkout / Order Issues
    {
        "subject": "Checkout error - unable to place order",
        "description": "I'm getting 'Something went wrong' error at checkout. Tried 3 times with different payment methods. Cart has items worth Rs 1,54,998.",
        "type": "technical_issue", "priority": "high", "channel": "chat",
    },
    {
        "subject": "Promo code not working",
        "description": "Promo code DIWALI2026 is showing 'Invalid code' but the banner on the website says it's valid till September 30.",
        "type": "billing", "priority": "medium", "channel": "chat",
    },
    # Hinglish / Casual Indian Style
    {
        "subject": "Order status enquiry",
        "description": "bhai mera order kab tak aa jayega? 5 din ho gaye order kiya tha, koi update nahi mila abhi tak.",
        "type": "technical_issue", "priority": "medium", "channel": "chat",
    },
    {
        "subject": "Refund kab milega?",
        "description": "Maine order cancel kiya tha 1 week pehle. Refund kab tak aayega mere account mein? Bahut pareshaan hoon.",
        "type": "refund", "priority": "high", "channel": "chat",
    },
    {
        "subject": "Product exchange karna hai",
        "description": "Headphone ka size mere liye comfortable nahi hai. Kya main exchange kar sakta hoon dusre model se?",
        "type": "inquiry", "priority": "medium", "channel": "chat",
    },
    # Escalation-worthy
    {
        "subject": "Extremely poor service - want to escalate",
        "description": "This is the 4th time I'm contacting support about my refund. Every time I'm told it will be processed in 3-5 days but nothing happens. I want to speak to a manager NOW.",
        "type": "technical_issue", "priority": "critical", "channel": "chat",
    },
    {
        "subject": "Fraud - charged for order I never placed",
        "description": "I see a charge of Rs 49,999 on my credit card for an order I never placed. This looks like fraud. I need this resolved IMMEDIATELY.",
        "type": "billing", "priority": "critical", "channel": "phone",
    },
    # General
    {
        "subject": "How to track my order?",
        "description": "I'm new to your website. How can I track my recent order? I received an order confirmation email but can't find tracking info.",
        "type": "inquiry", "priority": "low", "channel": "chat",
    },
    {
        "subject": "Compare ProPhone X14 vs X14 Lite",
        "description": "Can you help me understand the differences between ProPhone X14 and ProPhone X14 Lite? Which one is better value for money?",
        "type": "inquiry", "priority": "low", "channel": "chat",
    },
    # Shipping
    {
        "subject": "International shipping enquiry",
        "description": "I'm currently in Dubai but my permanent address is in Mumbai. Can I get the order delivered to my Dubai address?",
        "type": "inquiry", "priority": "medium", "channel": "email",
    },
    {
        "subject": "Change delivery address after order placed",
        "description": "I placed an order 2 hours ago with my office address but I need it delivered to my home address instead. Order is not shipped yet.",
        "type": "technical_issue", "priority": "medium", "channel": "chat",
    },
    # Return
    {
        "subject": "Return pickup not scheduled",
        "description": "I requested a return 5 days ago but no pickup has been arranged yet. I need someone to collect the item from my address.",
        "type": "technical_issue", "priority": "medium", "channel": "chat",
    },
    {
        "subject": "Return window expired by 1 day",
        "description": "My return window expired yesterday. I couldn't initiate the return earlier because I was travelling. Can you make an exception?",
        "type": "inquiry", "priority": "medium", "channel": "chat",
    },
    # EMI
    {
        "subject": "EMI option not showing at checkout",
        "description": "I have an HDFC credit card and expected no-cost EMI for the UltraBook Pro 15 but the EMI option is not appearing at checkout.",
        "type": "billing", "priority": "medium", "channel": "chat",
    },
    {
        "subject": "EMI amount incorrect",
        "description": "I opted for 6-month no-cost EMI for Rs 89,999 but the monthly charge showing is Rs 16,500 instead of Rs 14,999.",
        "type": "billing", "priority": "high", "channel": "chat",
    },
    # Positive feedback
    {
        "subject": "Great delivery experience!",
        "description": "Just wanted to say thank you! My order was delivered a day early and the packaging was excellent. Keep up the good work!",
        "type": "inquiry", "priority": "low", "channel": "chat",
    },
    {
        "subject": "Thanking the support team",
        "description": "Agent Priya helped me resolve my payment issue within 10 minutes. Really impressed with the service. 5 stars!",
        "type": "inquiry", "priority": "low", "channel": "chat",
    },
]


def generate_tickets(order_count: int) -> list[dict]:
    """
    Generate support tickets referencing valid order indices.
    Each ticket maps to a specific order (and thus a specific customer).
    """
    tickets = []
    scenarios = list(TICKET_SCENARIOS)  # Copy to avoid mutation
    random.shuffle(scenarios)

    # Use at most order_count tickets, and at most len(scenarios)
    count = min(len(scenarios), order_count)

    # Distribute tickets across orders (some orders get tickets, some don't)
    order_indices = random.sample(range(order_count), min(count, order_count))

    for i, order_idx in enumerate(order_indices):
        scenario = scenarios[i % len(scenarios)]
        agent = random.choice(SUPPORT_AGENTS)

        status_choices = ["open", "in_progress", "pending_customer", "closed"]
        status_weights = [0.30, 0.25, 0.15, 0.30]
        status = random.choices(status_choices, weights=status_weights, k=1)[0]

        satisfaction = None
        resolution = None
        if status == "closed":
            satisfaction = random.randint(1, 5)
            resolution = f"Issue resolved. {scenario['subject']} was addressed by the support team."

        tickets.append({
            "order_index": order_idx,
            "subject": scenario["subject"],
            "description": scenario["description"],
            "type": scenario["type"],
            "status": status,
            "priority": scenario["priority"],
            "channel": scenario["channel"],
            "assigned_agent": agent,
            "resolution": resolution,
            "satisfaction_rating": satisfaction,
        })

    return tickets


# ──────────────────────────────────────────────
#  Main Generator
# ──────────────────────────────────────────────
def generate_full_dataset() -> dict:
    """Generate the complete Indian customer support dataset."""
    customers = generate_customers(30)
    orders = generate_orders(customer_count=30, min_orders=1, max_orders=4)
    tickets = generate_tickets(order_count=len(orders))

    return {
        "products": PRODUCTS,
        "customers": customers,
        "orders": orders,
        "tickets": tickets,
    }


if __name__ == "__main__":
    data = generate_full_dataset()
    print(f"Generated dataset:")
    print(f"  Products:  {len(data['products'])}")
    print(f"  Customers: {len(data['customers'])}")
    print(f"  Orders:    {len(data['orders'])}")
    print(f"  Tickets:   {len(data['tickets'])}")

    # Print sample data
    print(f"\nSample customer: {data['customers'][0]}")
    print(f"Sample product:  {data['products'][0]['name']} -- Rs.{data['products'][0]['price']:,.2f}")
    print(f"Sample ticket:   {data['tickets'][0]['subject']}")
