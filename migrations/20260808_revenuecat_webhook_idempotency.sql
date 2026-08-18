-- Apply before deploying API code that processes RevenueCat webhooks.
-- RevenueCat delivers webhooks at least once, so retries must not repeat a
-- subscription-state transition.

create table if not exists public.revenuecat_webhook_events (
  event_id text primary key,
  event_timestamp_ms bigint not null,
  -- Keep the audit record even if the corresponding Supabase account was deleted.
  app_user_id text not null,
  event_type text not null,
  received_at timestamptz not null default now()
);

create index if not exists revenuecat_webhook_events_app_user_id_idx
  on public.revenuecat_webhook_events (app_user_id, event_timestamp_ms desc);

-- Mobile clients never access this audit table. The API uses the service role.
alter table public.revenuecat_webhook_events enable row level security;