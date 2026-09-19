-- Mantém o índice vetorial limitado a tópicos recém-criados, sem alterar o
-- fluxo do worker: o trigger replica cada novo tópico automaticamente.

create table if not exists public.topic_embedding_candidates (
  topic_id uuid primary key references public.topics(id) on delete cascade,
  canonical_title text not null,
  embedding vector(3072) not null,
  created_at timestamp not null
);

-- Também corrige uma tabela auxiliar vazia criada por uma execução anterior
-- desta migration, quando a dimensão ainda estava configurada como 768.
alter table public.topic_embedding_candidates
  alter column embedding type vector(3072)
  using embedding::vector(3072);

create index if not exists idx_topic_embedding_candidates_created_at
  on public.topic_embedding_candidates (created_at);

-- HNSW aceita no máximo 2.000 dimensões para `vector`. `halfvec` preserva as
-- 3.072 dimensões dos embeddings usando precisão de 16 bits e permite até
-- 4.000 dimensões no índice.
-- A tabela é pequena (somente candidatos das últimas 36 horas), portanto a
-- criação normal do índice não deve causar um bloqueio prolongado.
create index if not exists idx_topic_embedding_candidates_embedding_hnsw
  on public.topic_embedding_candidates
  using hnsw ((embedding::halfvec(3072)) halfvec_cosine_ops)
  with (m = 16, ef_construction = 64);

-- Necessário para encontrar rapidamente os artigos dos tópicos descartados na
-- manutenção semanal e remover somente sua referência ao tópico.
create index if not exists idx_articles_topic_id
  on public.articles (topic_id);

create or replace function public.sync_topic_embedding_candidate()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  -- Não reinsere tópicos expirados se algum campo for atualizado depois da
  -- limpeza periódica da tabela auxiliar.
  if new.embedding is not null
     and new.created_at > now() - interval '36 hours' then
    insert into public.topic_embedding_candidates (
      topic_id,
      canonical_title,
      embedding,
      created_at
    )
    values (
      new.id,
      new.canonical_title,
      new.embedding,
      new.created_at
    )
    on conflict (topic_id) do update
      set canonical_title = excluded.canonical_title,
          embedding = excluded.embedding,
          created_at = excluded.created_at;
  end if;

  return new;
end;
$$;

drop trigger if exists trg_sync_topic_embedding_candidate on public.topics;

create trigger trg_sync_topic_embedding_candidate
after insert or update of canonical_title, embedding on public.topics
for each row
execute function public.sync_topic_embedding_candidate();

-- Inclui na tabela auxiliar os tópicos que já existiam quando a migration foi
-- aplicada. Tópicos mais antigos não participam da busca.
insert into public.topic_embedding_candidates (
  topic_id,
  canonical_title,
  embedding,
  created_at
)
select
  id,
  canonical_title,
  embedding,
  created_at
from public.topics
where created_at > now() - interval '36 hours'
  and embedding is not null
on conflict (topic_id) do update
  set canonical_title = excluded.canonical_title,
      embedding = excluded.embedding,
      created_at = excluded.created_at;

create or replace function public.find_similar_topic(
  query_embedding vector,
  similarity_threshold double precision default 0.12,
  window_hours integer default 24
)
returns table (
  id uuid,
  canonical_title text,
  distance double precision
)
language sql
stable
set hnsw.ef_search = 100
as $$
  select
    candidate.topic_id as id,
    candidate.canonical_title,
    candidate.embedding::halfvec(3072) <=> query_embedding::halfvec(3072) as distance
  from public.topic_embedding_candidates as candidate
  where candidate.created_at > now() - make_interval(hours => window_hours)
    and candidate.embedding::halfvec(3072) <=> query_embedding::halfvec(3072) < similarity_threshold
  order by candidate.embedding::halfvec(3072) <=> query_embedding::halfvec(3072)
  limit 1;
$$;

-- Executar manualmente também é seguro: mantém o HNSW pequeno mesmo se uma
-- execução agendada falhar ou ficar desabilitada.
create or replace function public.delete_expired_topic_embedding_candidates()
returns integer
language plpgsql
security definer
set search_path = public
as $$
declare
  deleted_count integer;
begin
  delete from public.topic_embedding_candidates
  where created_at <= now() - interval '36 hours';

  get diagnostics deleted_count = row_count;
  return deleted_count;
end;
$$;

-- Remove tópicos que não viraram cluster: mais de 36 horas, exatamente um
-- artigo associado e nunca marcados como hot. O artigo é preservado; apenas
-- sua referência ao tópico é removida antes de apagar o tópico.
create or replace function public.delete_stale_single_article_topics()
returns integer
language plpgsql
security definer
set search_path = public
as $$
declare
  deleted_count integer;
begin
  with stale_topics as materialized (
    select topic.id
    from public.topics as topic
    where topic.created_at <= now() - interval '36 hours'
      and coalesce(topic.is_hot, false) = false
      and topic.article_count = 1
      and (
        select count(*)
        from public.articles as article
        where article.topic_id = topic.id
      ) = 1
  ),
  detached_articles as (
    update public.articles as article
    set topic_id = null
    from stale_topics
    where article.topic_id = stale_topics.id
    returning article.topic_id
  )
  delete from public.topics as topic
  using stale_topics
  where topic.id = stale_topics.id
    and not exists (
      select 1
      from public.articles as article
      where article.topic_id = topic.id
    );

  get diagnostics deleted_count = row_count;
  return deleted_count;
end;
$$;

-- A limpeza da tabela de busca precisa ser frequente para ela nunca acumular
-- uma semana de vetores. A poda de tópicos/artigos é semanal, aos domingos às
-- 03:30 UTC. O pg_cron é disponibilizado pelo Supabase.
create extension if not exists pg_cron;

do $$
declare
  job_id bigint;
begin
  for job_id in
    select jobid
    from cron.job
    where jobname in (
      'delete-expired-topic-embedding-candidates',
      'delete-stale-single-article-topics'
    )
  loop
    perform cron.unschedule(job_id);
  end loop;

  perform cron.schedule(
    'delete-expired-topic-embedding-candidates',
    '15 * * * *',
    'select public.delete_expired_topic_embedding_candidates();'
  );

  perform cron.schedule(
    'delete-stale-single-article-topics',
    '30 3 * * 0',
    'select public.delete_stale_single_article_topics();'
  );
end;
$$;

notify pgrst, 'reload schema';
