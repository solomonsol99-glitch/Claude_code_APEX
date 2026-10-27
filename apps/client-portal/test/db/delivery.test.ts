import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { as, createOrg, createUser, createTestDb, rejects, sendDelivery, type TestDb } from "./harness";

/** Server-side rules enforced inside the database: allowance, the under-100 rule, seeds, audit. */

let db: TestDb;
beforeAll(async () => {
  db = await createTestDb();
});
afterAll(async () => {
  await db?.drop();
});

const remaining = async (org: string) => {
  const { rows } = await db.pool.query(
    "select o.monthly_allowance - public.org_usage(o.id) as left from public.orgs o where id = $1",
    [org],
  );
  return Number(rows[0].left);
};

describe("monthly allowance", () => {
  it("allows sends up to the allowance, caps the last one, then refuses", async () => {
    const org = await createOrg(db.pool, "Capped", 10_000);
    expect(Number((await sendDelivery(db.pool, org, 6_000)).records)).toBe(6_000);
    const capped = await sendDelivery(db.pool, org, 9_000);
    expect(Number(capped.records)).toBe(4_000); // capped at what was left
    expect(await remaining(org)).toBe(0);
    const err = await rejects(sendDelivery(db.pool, org, 500));
    expect(err.hint).toBe("allowance_exhausted");
  });

  it("refuses when what is left is under 100", async () => {
    const org = await createOrg(db.pool, "Nearly full", 1_050);
    await sendDelivery(db.pool, org, 1_000);
    const err = await rejects(sendDelivery(db.pool, org, 1_000));
    expect(err.hint).toBe("under_minimum");
  });

  it("does not count failed or cancelled deliveries", async () => {
    const org = await createOrg(db.pool, "Refunds", 1_000);
    const d = await sendDelivery(db.pool, org, 1_000);
    await db.pool.query("update public.deliveries set status = 'failed' where id = $1", [d.id]);
    expect(await remaining(org)).toBe(1_000);
  });

  it("resets each calendar month", async () => {
    const org = await createOrg(db.pool, "Last month", 1_000);
    const d = await sendDelivery(db.pool, org, 1_000);
    await db.pool.query("update public.deliveries set created_at = now() - interval '40 days' where id = $1", [d.id]);
    expect(await remaining(org)).toBe(1_000);
  });

  it("holds under concurrent sends", async () => {
    const org = await createOrg(db.pool, "Race", 10_000);
    const results = await Promise.allSettled(
      Array.from({ length: 6 }, () => sendDelivery(db.pool, org, 4_000)),
    );
    const sent = results
      .filter((r): r is PromiseFulfilledResult<{ id: string; records: string; org_id: string }> => r.status === "fulfilled")
      .reduce((a, r) => a + Number(r.value.records), 0);
    expect(sent).toBe(10_000);
    expect(await remaining(org)).toBe(0);
  });

  it("refuses sends for a suspended org", async () => {
    const org = await createOrg(db.pool, "Paused", 10_000);
    await db.pool.query("update public.orgs set status = 'suspended' where id = $1", [org]);
    const err = await rejects(sendDelivery(db.pool, org, 1_000));
    expect(err.hint).toBe("org_inactive");
  });
});

describe("under-100 rule", () => {
  it("never creates a delivery under 100 records", async () => {
    const org = await createOrg(db.pool, "Tiny");
    const err = await rejects(sendDelivery(db.pool, org, 99));
    expect(err.hint).toBe("under_minimum");
  });

  it("never stores an audience estimated under 100", async () => {
    const org = await createOrg(db.pool, "Tiny 2");
    const err = await rejects(
      db.pool.query(
        `insert into public.audiences (org_id, name, filters, estimated_count, source) values ($1, 'Tiny', '{}', 99, 'portal')`,
        [org],
      ),
    );
    expect(err.code).toBe("23514"); // check_violation
  });
});

describe("seeds and audit", () => {
  it("gives every delivery its own seed tag and an audit entry", async () => {
    const org = await createOrg(db.pool, "Seeded");
    const a = await sendDelivery(db.pool, org, 500);
    const b = await sendDelivery(db.pool, org, 500);
    const { rows } = await db.pool.query(
      "select delivery_id, seed_tag from public.delivery_seeds where delivery_id = any($1)",
      [[a.id, b.id]],
    );
    expect(rows).toHaveLength(2);
    expect(rows[0].seed_tag).not.toBe(rows[1].seed_tag);
    const audit = await db.pool.query(
      "select detail from public.audit_log where action = 'delivery.create' and org_id = $1",
      [org],
    );
    expect(audit.rows).toHaveLength(2);
    expect(audit.rows[0].detail).toMatchObject({ records: 500, destination: "ad_platforms" });
  });

  it("audit log is append-only, even for the server", async () => {
    await db.pool.query("insert into public.audit_log (via, action) values ('system', 'test')");
    const err = await rejects(db.pool.query("update public.audit_log set action = 'edited'"));
    expect(err.message).toMatch(/append-only/);
    const err2 = await as(db.pool, "service_role", {}, (c) => rejects(c.query("delete from public.audit_log")));
    expect(err2.code).toBe("42501");
  });
});

describe("auth users", () => {
  it("creates a profile for each new auth user, not an admin", async () => {
    const id = await createUser(db.pool, "new@client.test");
    const { rows } = await db.pool.query("select email, is_admin from public.profiles where id = $1", [id]);
    expect(rows[0]).toEqual({ email: "new@client.test", is_admin: false });
  });
});
