-- Guarda o horário da próxima execução de triagem para coalescer artigos
-- recebidos em sequência no mesmo tópico. A tarefa Celery ignora execuções
-- cujo horário não coincida mais com este valor.

alter table public.topics
  add column if not exists fact_check_next_at timestamptz;

alter table public.topics
  add column if not exists fact_check_status text;

alter table public.topics
  drop constraint if exists topics_fact_check_status_allowed;

alter table public.topics
  add constraint topics_fact_check_status_allowed
  check (fact_check_status is null or fact_check_status in ('official', 'unverifiable'));

-- Tópicos antigos que já exibem apenas claims não verificáveis também podem
-- pular a triagem em matérias novas. Não inferimos estado quando não há claims
-- ou quando existe qualquer veredicto positivo/negativo legado.
update public.topics as topic
set fact_check_status = 'unverifiable'
where topic.initial_check = true
  and topic.fact_check_status is null
  and exists (
    select 1
    from public.claims as claim
    where claim.topic_id = topic.id
  )
  and not exists (
    select 1
    from public.claims as claim
    where claim.topic_id = topic.id
      and claim.verdict <> 'unverifiable'
  );

create index if not exists idx_topics_fact_check_next_at
  on public.topics (fact_check_next_at)
  where fact_check_next_at is not null;
