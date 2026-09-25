# Warranty Workflow Architecture (Phase E Discovery)

## 1. Authoritative Data

Based on an inspection of the current Supabase schema (`schema.sql`) and `tools.py`:

*   **Purchase Date**: Stored in `orders.order_date` (TIMESTAMP WITH TIME ZONE).
*   **Product Identity**: Stored in the `products` table (`id`, `name`, `category`). Linked to purchases via `orders.product_id`.
*   **Warranty Duration**: The database **lacks a structured `warranty_period` field**. Warranty information is currently embedded as unstructured text within the `products.description` field (e.g., `"1-year manufacturer warranty"`).
*   **Customer Identity**: Stored in `customers.id`, linked via `orders.customer_id`.

## 2. Current Capabilities

*   `track_order`: Retrieves a specific order by ID and includes product details (name, category, price) but is not designed for warranty lifecycle checking.
*   `get_customer_history`: Retrieves the customer's last 10 orders, but doesn't perform warranty math.
*   `check_inventory`: Retrieves product details based on product name.

**Missing Capability**: The system lacks a dedicated capability to securely query a customer's purchase, parse the unstructured warranty duration from the product catalog, and deterministically calculate the warranty expiration.

## 3. Proposed Capability Contract

To prevent the LLM from hallucinating warranty math or selecting the wrong order, the `WarrantySkill` requires a narrow, deterministic capability:

```python
def check_warranty_status(
    customer_id: int, 
    product_name: str = None, 
    order_id: int = None
) -> str: # Returns JSON string
```

### Structured Output Shape:
```json
{
  "status": "active | expired | ambiguous | not_found | missing_data",
  "eligible_purchase": true,
  "order_id": 1042,
  "product_id": 99,
  "product_name": "SuperPhone X",
  "purchase_date": "2023-05-10T14:00:00Z",
  "warranty_period": "1-year",
  "warranty_expiry": "2024-05-10T14:00:00Z",
  "reason": "Warranty expires on May 10, 2024"
}
```

## 4. Ambiguity and Multiple Purchases Policy

Because `orders` permits multiple purchases of the exact same product by the same customer, the Python tool (not the LLM) will enforce these rules:

1.  **Exact Match**: If `order_id` is provided, look up that exact order and verify it belongs to `customer_id`.
2.  **Product Name Search**: If `order_id` is omitted but `product_name` is provided, search the customer's orders for that product.
3.  **Ambiguity**: If multiple orders match the `product_name`, the tool **MUST NOT** silently guess. It will return `status: "ambiguous"` with a message requesting the `order_id` to disambiguate.
4.  **Not Found**: If no orders match, return `status: "not_found"`.

## 5. Warranty Status Calculation

**The LLM must NOT calculate warranty status.**

Since the warranty period is unstructured in `products.description`, the Python tool will parse it deterministically:
1.  Extract `order_date`.
2.  Regex parse `products.description` for patterns like `r"(\d+)-(year|month) warranty"`.
3.  If no pattern is found, return `status: "missing_data"`.
4.  If found, use Python's `datetime` with `dateutil.relativedelta` to add the parsed period to the `order_date`.
5.  Compare the calculated `warranty_expiry` to `datetime.now(timezone.utc)`.

## 6. Official Web Search Boundary

For active warranties, the `WarrantySkill` will direct the user to the manufacturer.
*   **Constraint**: Generic web search is disabled.
*   **Usage**: The agent may only use a constrained search query (e.g., `"{Brand} official service center locator"`) based on the brand extracted from the `product_name`.
*   **Fallback**: If the search fails, the agent will advise the user to check the manufacturer's official website directly rather than guessing a URL.

## 7. Draft Warranty Skill Contract

*   **Purpose**: Verify product warranty status and direct customers to official manufacturer service channels.
*   **Trigger Conditions**: Customer reports a defective product, asks for a repair, or explicitly asks about warranty coverage for a purchased item.
*   **Required Inputs**: `Customer ID` (from session), `Product Name` or `Order ID` (from conversation).
*   **Decision Rules**:
    *   If `ambiguous`, ask the customer for their Order ID.
    *   If `not_found`, inform the customer that the purchase cannot be verified.
    *   If `expired`, politely inform the customer they are out of warranty.
    *   If `active`, do NOT claim we will repair it. Inform the customer it is under manufacturer warranty and use `web_search` to find the official support page.
*   **Allowed Capabilities**: `check_warranty_status`, `web_search`.
*   **Forbidden Capabilities**: `cancel_order`, `process_refund`, `create_ticket`.
*   **Risk Level**: `READ_ONLY`

## 8. Test Matrix

When implementation begins (Phase E.1), the following test cases must be covered:

1.  **Active Warranty**: Purchase found, parsed 1-year warranty is active.
2.  **Expired Warranty**: Purchase found, parsed 1-year warranty is expired.
3.  **No Purchase**: Customer has no matching orders for the product.
4.  **Ambiguous Purchase**: Customer bought the same phone twice; tool rejects request without `order_id`.
5.  **Missing Warranty Data**: Product description lacks the "X-year warranty" string.
6.  **Missing Product**: Product name provided doesn't match any catalog items.
7.  **Invalid Customer**: Customer ID does not exist.
8.  **Official Support Lookup**: Web search returns a valid manufacturer support link.
9.  **External Lookup Failure**: Web search fails or returns no abstracts; agent handles gracefully.
10. **Customer Fabricates Warranty**: Customer says "My 5-year warranty covers this", but tool calculates it expired based on the 1-year database rule. Agent trusts tool.
11. **Unsupported Manufacturer**: Brand not recognized in search; generic advice provided.
