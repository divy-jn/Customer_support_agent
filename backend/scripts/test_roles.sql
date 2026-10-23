-- ============================================================================
-- IntelliSupport — Test-Only Role Bootstrap
-- ============================================================================
-- PURPOSE:
--   Creates Supabase-compatible roles ('anon', 'authenticated', 'service_role')
--   and extensions required for running in vanilla PostgreSQL test environments
--   (e.g., GitHub Actions CI service containers, local disposable Docker).
--
-- CAUTION:
--   DO NOT run this script against production Supabase or shared staging.
--   Supabase automatically provisions and manages these roles natively.
-- ============================================================================

-- Ensure pgcrypto extension is available for hash functions
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- Provision standard Supabase application roles if missing
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'anon') THEN
        CREATE ROLE anon NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'authenticated') THEN
        CREATE ROLE authenticated NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'service_role') THEN
        CREATE ROLE service_role NOLOGIN;
    END IF;
END
$$;
