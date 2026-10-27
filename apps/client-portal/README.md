# Coreset client portal

The client portal and AI connector at `app.coresetdata.com`. Clients build audiences from
Coreset data and send them to their channels. **They never download or view records.**

This app is separate from the Market Response Engine in the rest of this repo, and shares no code with it.

Stack: Next.js 16 (App Router, TypeScript), Tailwind 4, Supabase (Postgres + Auth), Vercel.

## Build status

| Step | What | Status |
|---|---|---|
| 1 | Scaffold, schema + RLS, auth, admin seed | Done; waiting on Supabase and Vercel accounts to deploy |
| 2 | Portal screens on the sample provider | Not started |
| 3 | Admin console | Not started |
| 4 | MCP connector + API keys | Not started |
| 5 | "Client login" button + DNS | Not started |
| 6 | Live Coreset API adapter | Waiting on `docs/coreset-api.md` |

## How access works

- **Clients** can only *read* their own org's rows (Row Level Security). They have no
  insert, update or delete rights on any table, so the public Supabase key is useless
  for writing. Every save, send and key change goes through server code that checks
  the rules first and writes with the service role.
- **Coreset admins** (`profiles.is_admin`) can read everything, but only from a session
  that passed two-step verification (`aal2`). Without it they get no admin access.
- **Deliveries** are created only by `create_delivery()` in the database. It locks the org,
  enforces the monthly allowance (capping the size at what's left), refuses anything
  under 100 records, issues a unique seed tag and writes the audit entry in one transaction.
- **Hidden from clients:** seed tags, decoy seeds, API key hashes and the audit log.
- **Audit log:** append-only, even for the service role.
- **No consumer records** are stored in this database: only filters, counts and delivery metadata.

## Local development

```bash
cd apps/client-portal
npm install
cp .env.example .env.local      # fill in your Supabase project's values
npm run dev                     # http://localhost:3000
```

Checks:

```bash
npm run lint && npm run typecheck && npm run build
DATABASE_URL=postgres://postgres@localhost:5432/postgres npm test
```

The database tests need a Postgres 16 server where the user can create databases. Each
test file creates and drops its own database, using `test/db/supabase-stub.sql` in place of
Supabase's `auth` schema. CI runs all of this (see the `client-portal` job in `.github/workflows/ci.yml`).

You can also run the full Supabase stack locally with Docker: `npx supabase start`
(config in `supabase/config.toml`).

## First-time setup (do once)

### 1. Supabase

1. Create a project at supabase.com (region: US East or Central).
2. Apply the schema: `npx supabase link --project-ref <ref>` then `npx supabase db push`.
   Or paste `supabase/migrations/*.sql` into the SQL editor, in order.
3. **Authentication → Sign In / Providers:** turn **off** "Allow new users to sign up".
   Only admins create accounts. Keep Email enabled.
4. **Authentication → Multi-Factor:** enable **TOTP (authenticator app)**.
5. **Authentication → URL Configuration:**
   - Site URL: `https://app.coresetdata.com`
   - Redirect URLs: `https://app.coresetdata.com/**`, `http://localhost:3000/**`, and your
     Vercel preview pattern, e.g. `https://*-<team>.vercel.app/**`
6. **Authentication → Email Templates:** paste the bodies of `supabase/templates/invite.html`,
   `magic_link.html` and `recovery.html` into Invite user, Magic Link and Reset Password.
   They send people to `/auth/confirm` with a token, so links work in any browser.
7. **Authentication → Emails → SMTP:** set up a real sender (e.g. Resend or Postmark) on
   `coresetdata.com`. Supabase's built-in mailer is heavily rate-limited and is only for testing.
8. **Project Settings → API Keys:** copy the project URL, the publishable key and a secret key
   into Vercel (next section). Never paste the secret key anywhere else.

### 2. Vercel

1. Import this GitHub repo. Set **Root Directory** to `apps/client-portal`.
2. Environment variables (Production and Preview): `SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEY`,
   `SUPABASE_SECRET_KEY`, `DATA_PROVIDER=sample`. In Production also set `APP_URL=https://app.coresetdata.com`.
3. Every push gets a preview URL; `main` deploys to production.
4. **Domains:** add `app.coresetdata.com`. At your DNS provider, create
   `CNAME  app  →  cname.vercel-dns.com` (Vercel shows the exact value).

### 3. First admin

```bash
SUPABASE_URL=... SUPABASE_SECRET_KEY=... APP_URL=https://app.coresetdata.com \
  npm run seed:admin -- peterashton9@icloud.com
```

The script sends an invite email. Set a password from the link, then set up an authenticator
app on the next screen. No password is ever stored in code or config.

## Adding a migration

Put new files in `supabase/migrations/` with a later timestamp. Supabase grants new tables to
`anon` and `authenticated` by default, so every migration that adds a table must:
`revoke all` from `anon, authenticated`, `grant select` to `authenticated` only where clients
should read, `enable row level security`, and add a read policy scoped by `current_org_id()`
or `is_admin()`. Add the table to `test/db/rls.test.ts`.
