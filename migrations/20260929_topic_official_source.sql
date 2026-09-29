-- Armazena uma única fonte oficial no nível do tópico. Claims legadas ficam
-- preservadas para compatibilidade de dados, mas deixam de ser o contrato público.
alter table public.topics
  add column if not exists official_source jsonb not null default jsonb_build_object(
    'status', 'unavailable',
    'label', 'Nenhuma fonte oficial aplicável',
    'sources', jsonb_build_array(),
    'scope', ''
  );

notify pgrst, 'reload schema';
