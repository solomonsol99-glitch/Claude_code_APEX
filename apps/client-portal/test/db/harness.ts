import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { randomBytes } from "node:crypto";
import pg from "pg";

const ROOT = join(__dirname, "..", "..");
const MIGRATIONS = join(ROOT, "supabase", "migrations");

function adminUrl(): string {
  const url = process.env.DATABASE_URL;
  if (!url) {
    throw new Error(
      "Database tests need DATABASE_URL pointing at a Postgres 16 server where the user can CREATE DATABASE " +
        "(e.g. postgres://postgres@localhost:5432/postgres).",
    );
  }
  return url;
}

export type TestDb = { pool: pg.Pool; drop: () => Promise<void> };

/** Creates a throwaway database with the Supabase stub and every migration applied. */
export async function createTestDb(): Promise<TestDb> {
  const name = "cp_test_" + randomBytes(6).toString("hex");
  const admin = new pg.Client({ connectionString: adminUrl() });
  await admin.connect();
  await admin.query(`create database ${name}`);
  await admin.end();

  const url = new URL(adminUrl());
  url.pathname = "/" + name;
  const pool = new pg.Pool({ connectionString: url.toString(), max: 6 });

  await pool.query(readFileSync(join(__dirname, "supabase-stub.sql"), "utf8"));
  for (const file of readdirSync(MIGRATIONS).filter((f) => f.endsWith(".sql")).sort()) {
    await pool.query(readFileSync(join(MIGRATIONS, file), "utf8"));
  }

  return {
    pool,
    drop: async () => {
      await pool.end();
      const c = new pg.Client({ connectionString: adminUrl() });
      await c.connect();
      await c.query(`drop database if exists ${name} with (force)`);
      await c.end();
    },
  };
}

export type Claims = { sub?: string; aal?: "aal1" | "aal2"; role?: string };

/**
 * Runs fn inside a transaction as a Supabase client role with the given JWT claims,
 * then rolls back. This is what a browser holding the public anon key can do.
 */
export async function as<T>(
  pool: pg.Pool,
  role: "anon" | "authenticated" | "service_role",
  claims: Claims,
  fn: (c: pg.PoolClient) => Promise<T>,
): Promise<T> {
  const c = await pool.connect();
  try {
    await c.query("begin");
    await c.query("select set_config('request.jwt.claims', $1, true)", [JSON.stringify({ role, ...claims })]);
    await c.query(`set local role ${role}`);
    return await fn(c);
  } finally {
    await c.query("rollback").catch(() => {});
    c.release();
  }
}

/** Expects the query to fail; returns the Postgres error. */
export async function rejects(p: Promise<unknown>): Promise<pg.DatabaseError> {
  try {
    await p;
  } catch (e) {
    return e as pg.DatabaseError;
  }
  throw new Error("expected query to fail, but it succeeded");
}

export async function createUser(pool: pg.Pool, email: string): Promise<string> {
  const { rows } = await pool.query<{ id: string }>("insert into auth.users (email) values ($1) returning id", [email]);
  return rows[0].id;
}

export async function createOrg(pool: pg.Pool, name: string, allowance = 1_000_000): Promise<string> {
  const { rows } = await pool.query<{ id: string }>(
    "insert into public.orgs (name, monthly_allowance) values ($1, $2) returning id",
    [name, allowance],
  );
  return rows[0].id;
}

export async function addMember(pool: pg.Pool, userId: string, orgId: string, role: "owner" | "member" = "owner") {
  await pool.query("insert into public.memberships (user_id, org_id, role) values ($1, $2, $3)", [userId, orgId, role]);
}

export async function sendDelivery(
  pool: pg.Pool | pg.PoolClient,
  orgId: string,
  requested: number,
  opts: { destination?: string; userId?: string | null } = {},
) {
  const { rows } = await pool.query(
    `select * from public.create_delivery($1, null, 'Test audience', '{}'::jsonb, $2::public.destination, $3, null, 'portal', $4, null)`,
    [orgId, opts.destination ?? "ad_platforms", requested, opts.userId ?? null],
  );
  return rows[0] as { id: string; records: string; org_id: string };
}
