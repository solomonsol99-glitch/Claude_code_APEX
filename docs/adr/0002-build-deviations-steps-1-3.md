# ADR 0002: Deviations from the blueprint in build steps 1–3

- Status: accepted (Peter approved items 4–7, 2026-10-21)
- Date: 2026-10-20
- Blueprint sections: §2.4, §2.7, §3.1, §1.4, §6, coding standards

## Context
The blueprint asks for an ADR for any design decision that deviates from it. These came
up while building the skeleton, `packages/core`, `packages/net` and `packages/engine_adapter`.

## Decisions

### Naming and layout
1. Python modules use an `mre_` prefix (`mre_core`, `mre_net`, `mre_security`,
   `mre_reporting`) to avoid clashing with generic names like `core`. `engine_adapter`
   keeps the blueprint's name.
2. Test files carry a package prefix (`test_core_*`, `test_ea_*`) and pytest runs with
   `--import-mode=importlib`.
3. `engine_adapter.testing` (fixtures, a fake MiroFish server, contract cases) ships
   inside the package so the worker, golden and chaos suites can reuse it.

### Tenant isolation (§3.1) — security-relevant, please review
4. The RLS policy is `tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid`.
   After a `SET LOCAL` transaction ends, Postgres resets the setting to `''`, not NULL,
   and the blueprint's plain `::uuid` cast then raises instead of matching nothing.
5. `mre_admin` gets a cross-tenant `USING (true)` policy instead of `BYPASSRLS`
   (`BYPASSRLS` needs a superuser to grant). `tenants` itself is under RLS keyed on `id`.
6. Sessions run `SET LOCAL ROLE mre_app` (or `mre_admin`) and re-apply the tenant on
   every transaction. Vault-issued login users must be members of those roles.
7. `audit_events.tenant_id` is nullable (NULL = platform chain, written by admins). There
   is one hash chain per tenant, serialized with an advisory lock and a unique
   `(tenant_id, seq)` index, because RLS stops a tenant from seeing a global chain.

### Job state machine (§1.4)
8. FAILED is reachable from any live state (watchdog, §4.5) and CANCELLED from any state
   before DELIVERED. PARTIAL is a flag (`jobs.is_partial`), not a state. Runs can restart
   to PENDING, or from SIMULATING back to PROFILES_READY (§4.5 restart rule).

### Tenant config (§6) — stricter than the example YAML
9. Enforced: `memory.delete_after_run`, `auth.oidc.require_mfa_for_admins` and
   `contract.forbidden_uses_acknowledged` must be true; `calibration.min_outcomes >= 5`;
   `review.auto_rerun_budget_fraction <= 0.5`; panel size 20–500;
   `simulation.engine` in {mirofish, baseline, mock}; `notifications` and `features`
   optional with notifications off by default.

### Engine adapter (§2.4)
10. Extra fields beyond §2.4.1 so adapters stay stateless: `RunHandle` carries run/tenant/
    job/arm IDs, platforms, rounds, target and actual agents and the agent→persona map;
    `RunStatus.state`; `InterviewAnswer.scope` (baseline answers are per segment);
    `PersonaAttributes.sources` (per-attribute provenance).
11. `supports_agent_count_control` is `Literal["exact","approximate","none"]` (the
    blueprint typed it `bool` but commented three values).
12. Engine errors live in `engine_adapter.errors` with a `kind` of transient / input /
    budget / engine_bug; they still need folding into `mre_core.errors`.
13. Non-idempotent engine calls (ontology, create, start, interview, report generate) are
    never retried at the HTTP layer; retries happen at stage level with a fresh engine.
14. Persona-to-agent mapping relies on entity names: the seed builder must name each
    dossier entity `"<display_name> (<card_id>)"` (`dossier_entity_name`), because the
    engine's saved profiles carry no entity type (see ADR 0001, A1/A2).
15. `cleanup` reports `memory_deleted_verified = None`; verifying on Zep's side that the
    graph is gone is the watchdog's job, since the adapter does not hold the Zep key (A6).

## Consequences
Items 4–7 change how isolation is enforced and fall under "change security controls";
they need Peter's sign-off before anything is deployed.
