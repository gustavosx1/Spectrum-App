-- Categorias editoriais dos tópicos.
-- Um tópico pode pertencer a mais de uma categoria, então usamos text[]
-- com CHECK em vez de enum simples.

alter table public.topics
  add column if not exists categories text[] not null default '{}';

alter table public.topics
  drop constraint if exists topics_categories_allowed;

alter table public.topics
  add constraint topics_categories_allowed
  check (
    categories <@ array[
      'Política',
      'Economia',
      'Tecnologia',
      'Mundo',
      'Esportes'
    ]::text[]
  );

create index if not exists idx_topics_categories
  on public.topics using gin (categories);
