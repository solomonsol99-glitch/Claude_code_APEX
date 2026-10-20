"""Row-level security policy SQL and checks (§3.1)."""

from __future__ import annotations

from sqlalchemy import Connection, text

TENANT_EXPR = "NULLIF(current_setting('app.tenant_id', true), '')::uuid"

# Tables (in public) that have a tenant_id column but lack RLS enabled AND forced.
MISSING_RLS_SQL = """
SELECT c.relname
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')
  AND (EXISTS (SELECT 1 FROM pg_attribute a
               WHERE a.attrelid = c.oid AND a.attname = 'tenant_id' AND NOT a.attisdropped)
       OR c.relname = 'tenants')
  AND NOT (c.relrowsecurity AND c.relforcerowsecurity)
ORDER BY c.relname
"""

# Installed by the migration; raises if any tenant table lacks forced RLS.
ASSERT_RLS_FUNCTION_SQL = f"""
CREATE OR REPLACE FUNCTION mre_assert_rls() RETURNS void
LANGUAGE plpgsql AS $$
DECLARE missing text;
BEGIN
  SELECT string_agg(relname, ', ') INTO missing FROM ({MISSING_RLS_SQL}) t;
  IF missing IS NOT NULL THEN
    RAISE EXCEPTION 'RLS not enabled and forced on tenant tables: %', missing;
  END IF;
END $$;
"""  # noqa: S608 - built from constants only


def tables_missing_rls(conn: Connection) -> list[str]:
    return [row[0] for row in conn.execute(text(MISSING_RLS_SQL))]


def enable_rls_statements(table: str, key_column: str = "tenant_id") -> list[str]:
    """DDL that enables + forces RLS with tenant and admin policies on ``table``."""
    cond = f"{key_column} = {TENANT_EXPR}"
    return [
        f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
        f"CREATE POLICY tenant_isolation ON {table} TO mre_app USING ({cond}) WITH CHECK ({cond})",
        f"CREATE POLICY admin_access ON {table} TO mre_admin USING (true) WITH CHECK (true)",
    ]
