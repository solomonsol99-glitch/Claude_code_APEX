-- Coreset client portal: initial schema.
--
-- Access model
--   * Clients (role `authenticated`) can only READ rows that belong to their own org.
--   * Clients have no INSERT/UPDATE/DELETE rights on any table. Every write goes through
--     the server (service role) after server-side checks: under-100 rule, allowance,
--     restricted selects, audit logging.
--   * Coreset admins read everything, but only from a session that passed MFA (aal2).
--   * The database holds filters and counts only. It never stores consumer records.

-- ---------------------------------------------------------------- types

create type public.org_status as enum ('active', 'suspended', 'cancelled');
create type public.member_role as enum ('owner', 'member');
create type public.destination as enum ('ad_platforms', 'email_deployment', 'direct_mail');
create type public.delivery_status as enum (
  'processing', 'scheduled', 'matching', 'preparing', 'live', 'at_printer', 'complete', 'failed', 'cancelled'
);
create type public.action_source as enum ('portal', 'ai', 'admin', 'system');
create type public.restricted_attribute as enum ('ethnicity', 'religion', 'health_medicare', 'political');
create type public.request_status as enum ('pending', 'approved', 'denied');
create type public.seed_channel as enum ('email', 'phone', 'postal', 'maid');

-- ---------------------------------------------------------------- tables

create table public.plans (
  id uuid primary key default gen_random_uuid(),
  name text not null unique,
  monthly_allowance bigint not null check (monthly_allowance >= 0),
  created_at timestamptz not null default now()
);

create table public.orgs (
  id uuid primary key default gen_random_uuid(),
  name text not null check (length(trim(name)) between 1 and 120),
  plan_id uuid references public.plans (id),
  -- Copied from the plan when the org is created; an admin can override it per org.
  monthly_allowance bigint not null check (monthly_allowance >= 0),
  renewal_date date,
  status public.org_status not null default 'active',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table public.profiles (
  id uuid primary key references auth.users (id) on delete cascade,
  email text not null,
  full_name text,
  is_admin boolean not null default false,
  created_at timestamptz not null default now()
);

-- One org per user (primary key on user_id).
create table public.memberships (
  user_id uuid primary key references public.profiles (id) on delete cascade,
  org_id uuid not null references public.orgs (id) on delete cascade,
  role public.member_role not null default 'member',
  created_at timestamptz not null default now()
);
create index memberships_org_idx on public.memberships (org_id);

create table public.api_keys (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.orgs (id) on delete cascade,
  name text not null default 'Default',
  key_hash text not null unique,          -- sha256 of the full key; the key itself is shown once
  last4 text not null check (length(last4) = 4),
  created_by uuid references public.profiles (id) on delete set null,
  created_at timestamptz not null default now(),
  last_used_at timestamptz,
  revoked_at timestamptz
);
create index api_keys_org_idx on public.api_keys (org_id);

create table public.audiences (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.orgs (id) on delete cascade,
  name text not null check (length(trim(name)) between 2 and 80),
  filters jsonb not null,
  estimated_count bigint not null check (estimated_count >= 100),  -- under-100 audiences are never saved
  source public.action_source not null,
  created_by uuid references public.profiles (id) on delete set null,
  api_key_id uuid references public.api_keys (id) on delete set null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index audiences_org_idx on public.audiences (org_id, updated_at desc);

create table public.deliveries (
  id uuid primary key default gen_random_uuid(),
  number bigint generated always as identity unique,   -- shown to clients as DLV-<number>
  org_id uuid not null references public.orgs (id) on delete cascade,
  audience_id uuid references public.audiences (id) on delete set null,
  audience_name text not null,
  filters jsonb not null,                                -- snapshot at send time
  destination public.destination not null,
  records bigint not null check (records >= 100),        -- under-100 deliveries are never sent
  send_date date,
  status public.delivery_status not null default 'processing',
  result text,
  source public.action_source not null,
  created_by uuid references public.profiles (id) on delete set null,
  api_key_id uuid references public.api_keys (id) on delete set null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index deliveries_org_idx on public.deliveries (org_id, created_at desc);

-- Leak tracing. Admin-only: clients never see their seed tag or the decoys.
create table public.delivery_seeds (
  delivery_id uuid primary key references public.deliveries (id) on delete cascade,
  org_id uuid not null references public.orgs (id) on delete cascade,
  seed_tag text not null unique,
  created_at timestamptz not null default now()
);

-- Decoy contacts Coreset controls. Assigned to deliveries so a leaked list can be traced.
create table public.seed_pool (
  id uuid primary key default gen_random_uuid(),
  channel public.seed_channel not null,
  value text not null,
  active boolean not null default true,
  created_at timestamptz not null default now(),
  unique (channel, value)
);

create table public.seed_assignments (
  delivery_id uuid not null references public.deliveries (id) on delete cascade,
  seed_id uuid not null references public.seed_pool (id) on delete restrict,
  primary key (delivery_id, seed_id)
);
create index seed_assignments_seed_idx on public.seed_assignments (seed_id);

create table public.restricted_requests (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.orgs (id) on delete cascade,
  attribute public.restricted_attribute not null,
  status public.request_status not null default 'pending',
  requested_by uuid references public.profiles (id) on delete set null,
  note text,
  decided_by uuid references public.profiles (id) on delete set null,
  decided_at timestamptz,
  created_at timestamptz not null default now()
);
create unique index restricted_requests_one_pending
  on public.restricted_requests (org_id, attribute) where status = 'pending';

-- Append-only. Holds filters and sizes, never record-level data.
create table public.audit_log (
  id bigint generated always as identity primary key,
  at timestamptz not null default now(),
  org_id uuid references public.orgs (id) on delete set null,
  actor_user_id uuid references public.profiles (id) on delete set null,
  actor_api_key_id uuid references public.api_keys (id) on delete set null,
  via public.action_source not null,
  action text not null,
  detail jsonb not null default '{}'::jsonb
);
create index audit_log_org_idx on public.audit_log (org_id, at desc);
create index audit_log_action_idx on public.audit_log (action, at desc);

-- ---------------------------------------------------------------- helpers

create function public.touch_updated_at() returns trigger
language plpgsql set search_path = '' as $$
begin
  new.updated_at := now();
  return new;
end $$;

create trigger orgs_touch before update on public.orgs
  for each row execute function public.touch_updated_at();
create trigger audiences_touch before update on public.audiences
  for each row execute function public.touch_updated_at();
create trigger deliveries_touch before update on public.deliveries
  for each row execute function public.touch_updated_at();

create function public.audit_log_is_append_only() returns trigger
language plpgsql set search_path = '' as $$
begin
  raise exception 'audit_log is append-only';
end $$;

create trigger audit_log_no_update before update or delete on public.audit_log
  for each row execute function public.audit_log_is_append_only();

-- The caller's org (null for admins without a membership and for anonymous callers).
create function public.current_org_id() returns uuid
language sql stable security definer set search_path = '' as $$
  select m.org_id from public.memberships m where m.user_id = auth.uid()
$$;

-- True only for a Coreset admin whose session passed MFA.
create function public.is_admin() returns boolean
language sql stable security definer set search_path = '' as $$
  select coalesce(auth.jwt() ->> 'aal', '') = 'aal2'
     and exists (select 1 from public.profiles p where p.id = auth.uid() and p.is_admin)
$$;

-- Start of the allowance month (calendar month, US Central time).
create function public.allowance_period_start(p_at timestamptz default now()) returns timestamptz
language sql immutable set search_path = '' as $$
  select date_trunc('month', p_at at time zone 'America/Chicago') at time zone 'America/Chicago'
$$;

-- Records delivered by an org in the allowance month containing p_at.
create function public.org_usage(p_org uuid, p_at timestamptz default now()) returns bigint
language sql stable security definer set search_path = '' as $$
  select coalesce(sum(d.records), 0)::bigint
  from public.deliveries d
  where d.org_id = p_org
    and d.status not in ('failed', 'cancelled')
    and d.created_at >= public.allowance_period_start(p_at)
    and d.created_at <  public.allowance_period_start(p_at) + interval '1 month'
$$;

-- Creates a profile row for every new auth user.
create function public.handle_new_user() returns trigger
language plpgsql security definer set search_path = '' as $$
begin
  insert into public.profiles (id, email, full_name)
  values (new.id, coalesce(new.email, ''), new.raw_user_meta_data ->> 'full_name')
  on conflict (id) do nothing;
  return new;
end $$;

create trigger on_auth_user_created after insert on auth.users
  for each row execute function public.handle_new_user();

-- The only way a delivery is created. Locks the org row so two sends at once cannot
-- both pass the allowance check, caps the size at what is left, issues a unique seed
-- tag, and writes the audit entry in the same transaction.
create function public.create_delivery(
  p_org uuid,
  p_audience uuid,
  p_audience_name text,
  p_filters jsonb,
  p_destination public.destination,
  p_requested bigint,
  p_send_date date,
  p_source public.action_source,
  p_user uuid,
  p_api_key uuid
) returns public.deliveries
language plpgsql security definer set search_path = '' as $$
declare
  v_org public.orgs;
  v_remaining bigint;
  v_records bigint;
  v_row public.deliveries;
  v_tag text;
begin
  select * into v_org from public.orgs where id = p_org for update;
  if not found then
    raise exception 'org not found' using errcode = 'P0002';
  end if;
  if v_org.status <> 'active' then
    raise exception 'org is not active' using errcode = 'P0001', hint = 'org_inactive';
  end if;

  v_remaining := v_org.monthly_allowance - public.org_usage(p_org);
  if v_remaining <= 0 then
    raise exception 'monthly allowance used up' using errcode = 'P0001', hint = 'allowance_exhausted';
  end if;

  v_records := least(p_requested, v_remaining);
  if v_records < 100 then
    raise exception 'deliverable audience is under 100' using errcode = 'P0001', hint = 'under_minimum';
  end if;

  insert into public.deliveries
    (org_id, audience_id, audience_name, filters, destination, records, send_date, source, created_by, api_key_id)
  values
    (p_org, p_audience, p_audience_name, p_filters, p_destination, v_records, p_send_date, p_source, p_user, p_api_key)
  returning * into v_row;

  v_tag := 'cs-' || substr(replace(gen_random_uuid()::text, '-', ''), 1, 16);
  insert into public.delivery_seeds (delivery_id, org_id, seed_tag) values (v_row.id, p_org, v_tag);

  insert into public.audit_log (org_id, actor_user_id, actor_api_key_id, via, action, detail)
  values (p_org, p_user, p_api_key, p_source, 'delivery.create', jsonb_build_object(
    'delivery_id', v_row.id, 'audience_id', p_audience, 'destination', p_destination,
    'requested', p_requested, 'records', v_records, 'remaining_before', v_remaining,
    'send_date', p_send_date, 'filters', p_filters));

  return v_row;
end $$;

-- ---------------------------------------------------------------- privileges

-- Start from nothing, then grant reads only.
revoke all on all tables in schema public from anon, authenticated;
revoke all on all sequences in schema public from anon, authenticated;
revoke execute on all functions in schema public from public, anon, authenticated;

grant select on public.plans, public.orgs, public.profiles, public.memberships,
  public.audiences, public.deliveries, public.restricted_requests,
  public.delivery_seeds, public.seed_pool, public.seed_assignments, public.audit_log
  to authenticated;

-- The key hash is never readable by a client, even for its own keys.
grant select (id, org_id, name, last4, created_by, created_at, last_used_at, revoked_at)
  on public.api_keys to authenticated;

grant execute on function public.current_org_id(), public.is_admin(),
  public.allowance_period_start(timestamptz) to authenticated;

-- The server (service role) does all writes.
grant all on all tables in schema public to service_role;
grant all on all sequences in schema public to service_role;
grant execute on all functions in schema public to service_role;
revoke execute on function public.org_usage(uuid, timestamptz) from public;
grant execute on function public.org_usage(uuid, timestamptz) to service_role;
revoke update, delete, truncate on public.audit_log from service_role;

-- ---------------------------------------------------------------- row level security

alter table public.plans enable row level security;
alter table public.orgs enable row level security;
alter table public.profiles enable row level security;
alter table public.memberships enable row level security;
alter table public.api_keys enable row level security;
alter table public.audiences enable row level security;
alter table public.deliveries enable row level security;
alter table public.delivery_seeds enable row level security;
alter table public.seed_pool enable row level security;
alter table public.seed_assignments enable row level security;
alter table public.restricted_requests enable row level security;
alter table public.audit_log enable row level security;

create policy plans_read on public.plans for select to authenticated
  using (public.is_admin() or id = (select o.plan_id from public.orgs o where o.id = public.current_org_id()));

create policy orgs_read on public.orgs for select to authenticated
  using (public.is_admin() or id = public.current_org_id());

create policy profiles_read on public.profiles for select to authenticated
  using (
    public.is_admin()
    or id = auth.uid()
    or id in (select m.user_id from public.memberships m where m.org_id = public.current_org_id())
  );

create policy memberships_read on public.memberships for select to authenticated
  using (public.is_admin() or org_id = public.current_org_id());

create policy api_keys_read on public.api_keys for select to authenticated
  using (public.is_admin() or org_id = public.current_org_id());

create policy audiences_read on public.audiences for select to authenticated
  using (public.is_admin() or org_id = public.current_org_id());

create policy deliveries_read on public.deliveries for select to authenticated
  using (public.is_admin() or org_id = public.current_org_id());

create policy restricted_requests_read on public.restricted_requests for select to authenticated
  using (public.is_admin() or org_id = public.current_org_id());

create policy delivery_seeds_admin on public.delivery_seeds for select to authenticated
  using (public.is_admin());
create policy seed_pool_admin on public.seed_pool for select to authenticated
  using (public.is_admin());
create policy seed_assignments_admin on public.seed_assignments for select to authenticated
  using (public.is_admin());
create policy audit_log_admin on public.audit_log for select to authenticated
  using (public.is_admin());

-- ---------------------------------------------------------------- seed data

insert into public.plans (name, monthly_allowance) values
  ('Annual license', 1000000),
  ('Starter', 100000);
