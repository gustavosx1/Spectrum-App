-- Busca eficiente de tópicos por título e resumo editorial.
-- Substitui varreduras ILIKE '%termo%' por índice GIN sobre tsvector em português.

create extension if not exists pg_trgm;

alter table public.topics
  add column if not exists search_vector tsvector
  generated always as (
    setweight(to_tsvector('portuguese', coalesce(canonical_title, '')), 'A')
    || setweight(to_tsvector('portuguese', coalesce(summary, '')), 'B')
  ) stored;

create index if not exists idx_topics_search_vector
  on public.topics using gin (search_vector);

create index if not exists idx_topics_recent_public_search
  on public.topics (created_at desc)
  where is_hot = true and initial_check = true;
