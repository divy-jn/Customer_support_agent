-- ============================================================================
-- IntelliSupport — Deterministic Test Fixtures Bootstrap
-- ============================================================================
-- PURPOSE:
--   Populates baseline test records (customers, products, orders) required by
--   real PostgreSQL concurrency, idempotency, outbox, and reconciliation tests.
--
-- CAUTION:
--   DO NOT run this script against production Supabase or live customer data.
--   These records are strictly synthetic test fixtures for integration suites.
-- ============================================================================

-- 1. Baseline Test Customers
-- Satisfies foreign key constraints for customer-scoped tests
INSERT INTO customers (id, name, email) VALUES
    (1, 'Test Customer One', 'customer1@test.local'),
    (2, 'Test Customer Two', 'customer2@test.local'),
    (3, 'Test Customer Three', 'customer3@test.local'),
    (4, 'Test Customer Four', 'customer4@test.local'),
    (5, 'Test Customer Five', 'customer5@test.local'),
    (6, 'Test Customer Six', 'customer6@test.local'),
    (10, 'Test Customer Ten', 'customer10@test.local'),
    (999, 'Test Customer 999', 'customer999@test.local'),
    (9999, 'Test Customer 9999', 'customer9999@test.local')
ON CONFLICT (id) DO NOTHING;

-- 2. Baseline Test Products
INSERT INTO products (id, name, category, price, stock, description) VALUES
    (1, 'Test Product', 'General', 100.00, 50, 'Standard test product')
ON CONFLICT (id) DO NOTHING;

-- 3. Baseline Test Orders
-- Statuses match state expectations in test_idempotency_real and test_postgres_concurrency_real
INSERT INTO orders (id, customer_id, product_id, status, tracking_number) VALUES
    (888, 1, 1, 'cancelled', 'TRK-888'),
    (999, 1, 1, 'active', 'TRK-999'),
    (1001, 1, 1, 'active', 'TRK-1001'),
    (4004, 1, 1, 'delivered', 'TRK-4004')
ON CONFLICT (id) DO NOTHING;

-- 4. Synchronize auto-increment sequences past hardcoded IDs
SELECT setval(pg_get_serial_sequence('customers', 'id'), coalesce(max(id), 1)) FROM customers;
SELECT setval(pg_get_serial_sequence('products', 'id'), coalesce(max(id), 1)) FROM products;
SELECT setval(pg_get_serial_sequence('orders', 'id'), coalesce(max(id), 1)) FROM orders;
