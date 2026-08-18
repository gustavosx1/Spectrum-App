-- Follow-up for installations where 20260807 was already applied.
-- The table already exists; only lock direct client access to the legacy
-- purchase-ownership data. The service role used by the API bypasses RLS.
alter table public.redeemed_purchases enable row level security;