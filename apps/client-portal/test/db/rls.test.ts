import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { addMember, as, createOrg, createUser, createTestDb, rejects, sendDelivery, type TestDb } from "./harness";

/**
 * Row Level Security: a client can only ever read its own org's rows, can write nothing
 * directly, and never sees seed tags, decoys, key hashes, or the audit log.
 */

let db: TestDb;
let orgA: string, orgB: string;
let alice: string, bob: string, admin: string, stray: string;

const CLIENT_TABLES = ["orgs", "memberships", "audiences", "deliveries", "api_keys", "restricted_requests"] as const;
const ADMIN_ONLY_TABLES = ["audit_log", "delivery_seeds", "seed_pool", "seed_assignments"] as const;
const API_KEY_COLS = "id, org_id, name, last4, created_at, last_used_at, revoked_at";
const cols = (t: string) => (t === "api_keys" ? API_KEY_COLS : "*");

beforeAll(async () => {
  db = await createTestDb();
  const p = db.pool;
  orgA = await createOrg(p, "Acme");
  orgB = await createOrg(p, "Globex");
  alice = await createUser(p, "alice@acme.test");
  bob = await createUser(p, "bob@globex.test");
  admin = await createUser(p, "admin@coreset.test");
  stray = await createUser(p, "nobody@nowhere.test");
  await addMember(p, alice, orgA);
  await addMember(p, bob, orgB);
  await p.query("update public.profiles set is_admin = true where id = $1", [admin]);

  for (const [org, user] of [[orgA, alice], [orgB, bob]]) {
    await p.query(
      `insert into public.audiences (org_id, name, filters, estimated_count, source, created_by)
       values ($1, 'Aud', '{"states":["TX"]}', 5000, 'portal', $2)`,
      [org, user],
    );
    await p.query(
      `insert into public.api_keys (org_id, key_hash, last4, created_by) values ($1::uuid, md5($1::text), 'abcd', $2)`,
      [org, user],
    );
    await p.query(
      `insert into public.restricted_requests (org_id, attribute, requested_by) values ($1, 'ethnicity', $2)`,
      [org, user],
    );
    await sendDelivery(p, org, 1000, { userId: user });
  }
  await p.query(`insert into public.seed_pool (channel, value) values ('email', 'decoy@coreset.test')`);
});

afterAll(async () => {
  await db?.drop();
});

describe("org isolation", () => {
  for (const table of CLIENT_TABLES) {
    it(`${table}: each client sees only its own org`, async () => {
      for (const [user, own] of [[alice, orgA], [bob, orgB]]) {
        const rows = await as(db.pool, "authenticated", { sub: user, aal: "aal1" }, async (c) =>
          (await c.query(`select ${cols(table)} from public.${table}`)).rows,
        );
        expect(rows.length).toBeGreaterThan(0);
        for (const r of rows) expect(table === "orgs" ? r.id : r.org_id).toBe(own);
      }
    });
  }

  it("profiles: a client sees itself and teammates, not other orgs", async () => {
    const ids = await as(db.pool, "authenticated", { sub: alice }, async (c) =>
      (await c.query("select id from public.profiles")).rows.map((r) => r.id),
    );
    expect(ids).toEqual([alice]);
  });

  it("a client cannot read another org's row even by id", async () => {
    const rows = await as(db.pool, "authenticated", { sub: alice }, async (c) =>
      (await c.query("select * from public.deliveries where org_id = $1", [orgB])).rows,
    );
    expect(rows).toEqual([]);
  });

  it("a signed-in user with no org sees nothing", async () => {
    await as(db.pool, "authenticated", { sub: stray }, async (c) => {
      for (const t of CLIENT_TABLES) expect((await c.query(`select ${cols(t)} from public.${t}`)).rows).toEqual([]);
    });
  });
});

describe("admin-only data", () => {
  for (const table of ADMIN_ONLY_TABLES) {
    it(`${table}: hidden from clients`, async () => {
      const rows = await as(db.pool, "authenticated", { sub: alice, aal: "aal2" }, async (c) =>
        (await c.query(`select * from public.${table}`)).rows,
      );
      expect(rows).toEqual([]);
    });
  }

  it("api key hashes are not readable by clients", async () => {
    const err = await as(db.pool, "authenticated", { sub: alice }, (c) =>
      rejects(c.query("select key_hash from public.api_keys")),
    );
    expect(err.code).toBe("42501"); // insufficient_privilege
  });

  it("an admin without MFA (aal1) gets no admin access", async () => {
    await as(db.pool, "authenticated", { sub: admin, aal: "aal1" }, async (c) => {
      expect((await c.query("select * from public.deliveries")).rows).toEqual([]);
      expect((await c.query("select * from public.audit_log")).rows).toEqual([]);
    });
  });

  it("an admin with MFA (aal2) sees every org and the audit log", async () => {
    await as(db.pool, "authenticated", { sub: admin, aal: "aal2" }, async (c) => {
      const orgs = (await c.query("select org_id from public.deliveries")).rows.map((r) => r.org_id);
      expect(new Set(orgs)).toEqual(new Set([orgA, orgB]));
      expect((await c.query("select * from public.audit_log")).rows.length).toBeGreaterThan(0);
      expect((await c.query("select * from public.delivery_seeds")).rows.length).toBe(2);
    });
  });
});

describe("clients cannot write directly", () => {
  const attempts: [string, string][] = [
    ["insert audience", `insert into public.audiences (org_id, name, filters, estimated_count, source) values ('${"$ORG"}', 'x y', '{}', 100, 'portal')`],
    ["update delivery status", "update public.deliveries set status = 'complete'"],
    ["raise own allowance", "update public.orgs set monthly_allowance = 999999999"],
    ["promote self to admin", "update public.profiles set is_admin = true"],
    ["join another org", `insert into public.memberships (user_id, org_id) values ('${"$USER"}', '${"$OTHER"}')`],
    ["approve own restricted request", "update public.restricted_requests set status = 'approved'"],
    ["insert api key", `insert into public.api_keys (org_id, key_hash, last4) values ('${"$ORG"}', 'x', '1234')`],
    ["delete deliveries", "delete from public.deliveries"],
    ["write audit log", "insert into public.audit_log (via, action) values ('portal', 'fake')"],
    ["call create_delivery", `select public.create_delivery('${"$ORG"}', null, 'x', '{}', 'ad_platforms', 1000, null, 'portal', null, null)`],
    ["call org_usage", `select public.org_usage('${"$OTHER"}')`],
  ];
  for (const [label, sql] of attempts) {
    it(label, async () => {
      const q = sql.replaceAll("$ORG", orgA).replaceAll("$OTHER", orgB).replaceAll("$USER", alice);
      const err = await as(db.pool, "authenticated", { sub: alice, aal: "aal2" }, (c) => rejects(c.query(q)));
      expect(err.code).toBe("42501");
    });
  }
});

describe("anonymous callers", () => {
  for (const table of [...CLIENT_TABLES, ...ADMIN_ONLY_TABLES, "profiles", "plans"]) {
    it(`${table}: no access with only the public anon key`, async () => {
      const err = await as(db.pool, "anon", {}, (c) => rejects(c.query(`select 1 from public.${table}`)));
      expect(err.code).toBe("42501");
    });
  }
});
