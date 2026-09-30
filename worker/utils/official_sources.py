from __future__ import annotations

import asyncio
import re
import unicodedata
import xml.etree.ElementTree as ET
import zipfile
from io import BytesIO

import httpx


"""Bounded connectors for public Brazilian primary-source APIs.

Only records returned by the APIs in this module can become direct verification
sources. Dataset-catalog results are deliberately marked as discovery leads:
metadata says that a dataset exists, not that it proves a claim.
"""

OFFICIAL_SOURCE_TIMEOUT_SECONDS = 15
MAX_DIRECT_EVIDENCE_ITEMS = 5

TREASURY_DPF_WORKBOOK_URL = (
    "https://www.tesourotransparente.gov.br/ckan/dataset/"
    "0998f610-bc25-4ce3-b32c-a873447500c2/resource/"
    "0402cb77-5e4c-4414-966f-0e87d802a29a/download/"
    "estoque-da-divida-publica-federal.xlsx"
)
TSE_CKAN_SEARCH_URL = "https://dadosabertos.tse.jus.br/api/3/action/package_search"
CAMARA_PROPOSICOES_URL = "https://dadosabertos.camara.leg.br/api/v2/proposicoes"
BCB_SERIES_URL = "https://api.bcb.gov.br/dados/serie/bcdata.sgs.{code}/dados/ultimos/6"
IBGE_IPCA_URL = (
    "https://servicodados.ibge.gov.br/api/v3/agregados/1737/periodos/-6/"
    "variaveis/2265?localidades=N1%5Ball%5D"
)

# These landing pages are deliberately fixed and auditable. They are only used
# as *possible* sources when a precise official record was not available; they
# never count as direct evidence or increase verification confidence.
PROBABLE_SOURCE_URLS = {
    "elections": "https://divulgacandcontas.tse.jus.br/divulga/",
    "electoral_polls": "https://pesqele-divulgacao.tse.jus.br/app/pesquisa/listar.xhtml",
    "tse_jurisprudence": "https://www.tse.jus.br/jurisprudencia/pesquisa-de-jurisprudencia",
    "sao_paulo_official_gazette": "https://diariooficial.prefeitura.sp.gov.br/",
    "camara": "https://www.camara.leg.br/",
    "senate": "https://www25.senado.leg.br/web/atividade/materias",
    "judiciary": "https://portal.stf.jus.br/",
    "official_gazette": "https://www.in.gov.br/leiturajornal",
    "ibge": "https://www.ibge.gov.br/estatisticas/",
    "bcb": "https://www.bcb.gov.br/estatisticas",
    "treasury": "https://www.tesourotransparente.gov.br/",
    "revenue": "https://www.gov.br/receitafederal/pt-br",
    "federal_government": "https://www.gov.br/",
    "transparency": "https://portaldatransparencia.gov.br/",
    "federal_police": "https://www.gov.br/pf/pt-br",
}


# The codes are deliberately narrow. A connector never guesses a BCB series
# identifier from a sentence, as an incorrect series would be worse than no
# verification at all.
BCB_SERIES_BY_TOPIC = {
    "selic": ("432", "Série SGS 432"),
    "juros": ("432", "Série SGS 432"),
    "cambio": ("1", "Série SGS 1"),
    "dolar": ("1", "Série SGS 1"),
}

TREASURY_KEYWORDS = {
    "divida",
    "tesouro",
    "titulo",
    "titulos",
    "dpf",
    "orcamento",
    "orcamentaria",
    "deficit",
    "superavit",
}
TSE_KEYWORDS = {
    "tse",
    "candidato",
    "candidatura",
    "candidaturas",
    "eleicao",
    "eleitoral",
    "urna",
    "prefeito",
    "governador",
    "vereador",
    "senador",
    "presidente",
    "partido",
}
ELECTION_DATA_KEYWORDS = {
    "candidato",
    "candidata",
    "candidatura",
    "candidaturas",
    "urna",
    "partido",
    "conta",
    "contas",
    "prestacao",
    "resultado",
    "resultados",
}
CANDIDACY_RECORD_KEYWORDS = {
    "candidato",
    "candidata",
    "candidatura",
    "candidaturas",
}
ELECTORAL_POLL_KEYWORDS = {
    "pesquisa",
    "pesquisas",
    "atlasintel",
    "realtime",
    "real",
    "time",
    "palver",
    "gerp",
    "intencao",
    "intencoes",
    "registro",
}
ELECTORAL_DECISION_KEYWORDS = {
    "decisao",
    "decisoes",
    "acordao",
    "acordaos",
    "julgamento",
    "julgamentos",
    "julga",
    "julgou",
    "voto",
    "votos",
    "vota",
    "sessao",
    "sessoes",
    "remocao",
    "posts",
    "propaganda",
}
SAO_PAULO_MUNICIPAL_KEYWORDS = {
    "prefeitura",
    "municipio",
    "municipal",
    "paulistana",
    "paulistano",
}
FEDERAL_ACT_KEYWORDS = {
    "governo",
    "federal",
    "uniao",
    "presidencia",
    "presidente",
    "ministerio",
    "ministerios",
}
CAMARA_KEYWORDS = {
    "camara",
    "deputado",
    "deputados",
    "projeto",
    "pl",
    "pec",
    "votacao",
    "comissao",
    "relator",
}
IBGE_IPCA_KEYWORDS = {"ipca", "inflacao", "inflacionario"}
IBGE_KEYWORDS = IBGE_IPCA_KEYWORDS | {
    "ibge",
    "pnad",
    "desemprego",
    "emprego",
    "populacao",
    "rendimento",
}
JUDICIARY_KEYWORDS = {
    "stf",
}
OFFICIAL_GAZETTE_KEYWORDS = {
    "diario",
    "dou",
    "nomeado",
    "nomeacao",
    "publicado",
    "portaria",
    "decreto",
}
REVENUE_KEYWORDS = {"receita", "imposto", "tributo", "tributaria", "irpf"}
FEDERAL_POLICE_KEYWORDS = {"policia", "pf", "inquerito", "mandado", "operacao"}
TRANSPARENCY_KEYWORDS = {"transparencia", "gasto", "despesa", "contrato", "convenio"}
CAMARA_QUERY_STOP_WORDS = CAMARA_KEYWORDS | {
    "apresentou",
    "apresentada",
    "aprovou",
    "aprovada",
    "rejeitou",
    "rejeitada",
    "federal",
    "sobre",
}


def _normalized_words(text: str) -> set[str]:
    normalized = unicodedata.normalize("NFD", text.casefold())
    normalized = "".join(
        character for character in normalized if unicodedata.category(character) != "Mn"
    )
    return set(re.findall(r"[a-z0-9]+", normalized))


def _clean_excerpt(value: object, limit: int = 650) -> str:
    if not isinstance(value, str):
        return ""
    text = " ".join(value.split())
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0] + "…"


def _excel_column(cell_reference: str) -> str:
    return "".join(character for character in cell_reference if character.isalpha())


def _xlsx_rows(workbook: bytes) -> list[dict[str, str]]:
    """Read the small Treasury XLSX with only Python's standard library."""
    namespace = {"main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with zipfile.ZipFile(BytesIO(workbook)) as archive:
        shared_root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
        shared_strings = [
            "".join(item.itertext())
            for item in shared_root.findall("main:si", namespace)
        ]
        sheet = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))

    rows: list[dict[str, str]] = []
    for row in sheet.findall(".//main:sheetData/main:row", namespace):
        values: dict[str, str] = {}
        for cell in row.findall("main:c", namespace):
            reference = cell.get("r", "")
            column = _excel_column(reference)
            value = cell.findtext("main:v", default="", namespaces=namespace)
            if cell.get("t") == "s" and value.isdigit():
                value = shared_strings[int(value)]
            if column and value.strip():
                values[column] = value.strip()
        rows.append(values)
    return rows


def _treasury_dpf_excerpt(workbook: bytes) -> str:
    rows = _xlsx_rows(workbook)
    # The published workbook uses row 5 for monthly headers and contains the
    # aggregate stock in the line headed "DPF EM PODER DO PÚBLICO".
    headers = next(
        (
            row
            for row in rows
            if "A" not in row
            and any(re.fullmatch(r"[A-Za-zÇç]{3}/\d{2}", value) for value in row.values())
        ),
        {},
    )
    dpf_row = next(
        (
            row
            for row in rows
            if row.get("A", "").strip().casefold() == "dpf em poder do público"
        ),
        {},
    )
    months = [(column, value) for column, value in headers.items() if column != "A"][-3:]
    observations = [
        f"{month}: {dpf_row[column]}"
        for column, month in months
        if column in dpf_row
    ]
    if not observations:
        return ""
    return (
        "Planilha oficial 'Estoque da Dívida Pública Federal', linha "
        "'DPF EM PODER DO PÚBLICO': "
        + "; ".join(observations)
        + "."
    )


def _direct_evidence(source_name: str, source_url: str, excerpt: str) -> dict[str, str]:
    return {
        "source_name": source_name,
        "source_url": source_url,
        "excerpt": excerpt,
        "kind": "direct_evidence",
    }


def _discovery_lead(source_name: str, source_url: str, excerpt: str) -> dict[str, str]:
    return {
        "source_name": source_name,
        "source_url": source_url,
        "excerpt": excerpt,
        "kind": "discovery_lead",
    }


def probable_official_source_urls(claim: str) -> list[str]:
    """Return fixed official starting points inferred from a claim's subject.

    These URLs are intentionally broad. They help a reader continue a manual
    consultation after an automated lookup fails, without representing that a
    record was found or that the claim was verified.
    """
    words = _normalized_words(claim)
    candidates: list[str] = []

    def add(key: str) -> None:
        url = PROBABLE_SOURCE_URLS[key]
        if url not in candidates:
            candidates.append(url)

    has_tse = "tse" in words
    has_electoral_context = has_tse or bool(words & {"eleicao", "eleicoes", "eleitoral"})

    # Election data, judicial decisions, and polling registrations are handled
    # by different TSE services. Never use a generic election keyword as a
    # reason to send a reader to DivulgaCandContas.
    if has_tse and words & ELECTORAL_DECISION_KEYWORDS:
        add("tse_jurisprudence")
    elif has_electoral_context and words & ELECTORAL_POLL_KEYWORDS:
        add("electoral_polls")
    elif words & CANDIDACY_RECORD_KEYWORDS or (
        has_electoral_context and words & ELECTION_DATA_KEYWORDS
    ):
        add("elections")
    elif "sao" in words and "paulo" in words and words & SAO_PAULO_MUNICIPAL_KEYWORDS:
        add("sao_paulo_official_gazette")
    elif words & JUDICIARY_KEYWORDS:
        add("judiciary")
    elif words & FEDERAL_ACT_KEYWORDS and words & OFFICIAL_GAZETTE_KEYWORDS:
        add("official_gazette")
    elif {"senador", "senado"} & words:
        add("senate")
    elif words & CAMARA_KEYWORDS:
        add("camara")
    elif words & IBGE_KEYWORDS:
        add("ibge")
    elif words & BCB_SERIES_BY_TOPIC.keys():
        add("bcb")
    elif words & TREASURY_KEYWORDS:
        add("treasury")
    elif words & OFFICIAL_GAZETTE_KEYWORDS:
        # A decree or ordinance without an identified authority is too
        # ambiguous to attach a federal publication portal safely.
        pass
    elif words & REVENUE_KEYWORDS:
        add("revenue")
    elif words & FEDERAL_POLICE_KEYWORDS:
        add("federal_police")
    elif words & TRANSPARENCY_KEYWORDS:
        add("transparency")
    return candidates[:1]


async def _treasury_dpf(client: httpx.AsyncClient) -> list[dict[str, str]]:
    response = await client.get(TREASURY_DPF_WORKBOOK_URL)
    response.raise_for_status()
    excerpt = _treasury_dpf_excerpt(response.content)
    return [_direct_evidence("Tesouro Nacional", TREASURY_DPF_WORKBOOK_URL, excerpt)] if excerpt else []


async def _bcb_series(client: httpx.AsyncClient, words: set[str]) -> list[dict[str, str]]:
    selected = next(
        (series for term, series in BCB_SERIES_BY_TOPIC.items() if term in words),
        None,
    )
    if not selected:
        return []
    code, label = selected
    source_url = BCB_SERIES_URL.format(code=code)
    response = await client.get(source_url, params={"formato": "json"})
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list):
        return []
    observations = [
        f"{item.get('data')}: {item.get('valor')}"
        for item in payload[-3:]
        if isinstance(item, dict) and item.get("data") and item.get("valor")
    ]
    if not observations:
        return []
    return [
        _direct_evidence(
            "Banco Central do Brasil",
            f"{source_url}?formato=json",
            f"{label}; últimas observações retornadas pela API oficial: "
            + "; ".join(observations)
            + ".",
        )
    ]


async def _ibge_ipca(client: httpx.AsyncClient) -> list[dict[str, str]]:
    response = await client.get(IBGE_IPCA_URL)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list) or not payload or not isinstance(payload[0], dict):
        return []
    variable = payload[0]
    series = (
        variable.get("resultados", [{}])[0].get("series", [{}])[0].get("serie", {})
        if isinstance(variable.get("resultados"), list) and variable.get("resultados")
        else {}
    )
    if not isinstance(series, dict):
        return []
    observations = [f"{period}: {value}" for period, value in list(series.items())[-3:]]
    if not observations:
        return []
    name = _clean_excerpt(variable.get("variavel"), 180)
    unit = _clean_excerpt(variable.get("unidade"), 30)
    return [
        _direct_evidence(
            "IBGE SIDRA",
            IBGE_IPCA_URL,
            f"{name} ({unit}), Brasil: " + "; ".join(observations) + ".",
        )
    ]


async def _camara_propositions(
    client: httpx.AsyncClient, claim: str
) -> list[dict[str, str]]:
    normalized = unicodedata.normalize("NFD", claim.casefold())
    normalized = "".join(
        character for character in normalized if unicodedata.category(character) != "Mn"
    )
    query_terms = [
        word
        for word in re.findall(r"[a-z0-9]+", normalized)
        if len(word) >= 4 and word not in CAMARA_QUERY_STOP_WORDS
    ][:3]
    if not query_terms:
        return []
    response = await client.get(
        CAMARA_PROPOSICOES_URL,
        params={
            "keywords": " ".join(query_terms),
            "itens": 3,
            "ordem": "DESC",
            "ordenarPor": "id",
            "formato": "json",
        },
    )
    response.raise_for_status()
    payload = response.json()
    records = payload.get("dados") if isinstance(payload, dict) else None
    if not isinstance(records, list):
        return []
    evidence: list[dict[str, str]] = []
    for record in records[:3]:
        if not isinstance(record, dict) or not isinstance(record.get("uri"), str):
            continue
        identifier = " ".join(
            str(record.get(key, "")) for key in ("siglaTipo", "numero", "ano")
        ).strip()
        ementa = _clean_excerpt(record.get("ementa"), 460)
        date = _clean_excerpt(record.get("dataApresentacao"), 80)
        if not identifier or not ementa:
            continue
        evidence.append(
            _direct_evidence(
                "Câmara dos Deputados",
                record["uri"],
                f"Proposição {identifier}; apresentada em {date}. Ementa: {ementa}",
            )
        )
    return evidence


async def _tse_catalog_leads(client: httpx.AsyncClient, claim: str) -> list[dict[str, str]]:
    response = await client.get(
        TSE_CKAN_SEARCH_URL,
        params={"rows": 3, "q": claim[:180]},
    )
    response.raise_for_status()
    payload = response.json()
    result = payload.get("result") if isinstance(payload, dict) else None
    datasets = result.get("results") if isinstance(result, dict) else None
    if not isinstance(datasets, list):
        return []
    leads: list[dict[str, str]] = []
    for dataset in datasets[:3]:
        if not isinstance(dataset, dict) or not isinstance(dataset.get("name"), str):
            continue
        url = f"https://dadosabertos.tse.jus.br/dataset/{dataset['name']}"
        title = _clean_excerpt(dataset.get("title"), 180)
        notes = _clean_excerpt(dataset.get("notes"), 430)
        if title:
            leads.append(_discovery_lead("TSE Dados Abertos", url, f"{title}. {notes}"))
    return leads


async def find_official_source_evidence(claim: str) -> list[dict[str, str]]:
    """Retrieve bounded primary-source context selected from the claim's topic.

    Failures are isolated per connector. Missing or malformed official responses
    simply remove that source from this run; they never become a model fact.
    """
    words = _normalized_words(claim)
    calls: list[object] = []
    if words & TREASURY_KEYWORDS:
        calls.append("treasury")
    if words & BCB_SERIES_BY_TOPIC.keys():
        calls.append("bcb")
    if words & IBGE_IPCA_KEYWORDS:
        calls.append("ibge")
    if words & CAMARA_KEYWORDS:
        calls.append("camara")
    if words & TSE_KEYWORDS:
        calls.append("tse")
    if not calls:
        return []

    async with httpx.AsyncClient(
        timeout=OFFICIAL_SOURCE_TIMEOUT_SECONDS,
        follow_redirects=True,
        headers={"Accept": "application/json", "User-Agent": "SpectrumOfficialVerifier/1.0"},
    ) as client:
        tasks = []
        for connector in calls:
            if connector == "treasury":
                tasks.append(_treasury_dpf(client))
            elif connector == "bcb":
                tasks.append(_bcb_series(client, words))
            elif connector == "ibge":
                tasks.append(_ibge_ipca(client))
            elif connector == "camara":
                tasks.append(_camara_propositions(client, claim))
            elif connector == "tse":
                tasks.append(_tse_catalog_leads(client, claim))
        responses = await asyncio.gather(*tasks, return_exceptions=True)

    evidence: list[dict[str, str]] = []
    for response in responses:
        if isinstance(response, Exception):
            continue
        evidence.extend(response)
    return evidence[:MAX_DIRECT_EVIDENCE_ITEMS]


def format_official_source_context(evidence: list[dict[str, str]]) -> str:
    if not evidence:
        return "Nenhuma resposta direta de API oficial ficou disponível nesta execução."
    sections: list[str] = []
    for item in evidence:
        label = "EVIDÊNCIA DIRETA" if item.get("kind") == "direct_evidence" else "REFERÊNCIA DE CATÁLOGO"
        sections.append(
            f"[{label}: {item.get('source_name', 'órgão público')}]\n"
            f"URL: {item.get('source_url', '')}\n"
            f"Conteúdo retornado: {item.get('excerpt', '')}"
        )
    return "\n\n".join(sections)


def direct_evidence_urls(evidence: list[dict[str, str]]) -> set[str]:
    return {
        item["source_url"]
        for item in evidence
        if item.get("kind") == "direct_evidence" and isinstance(item.get("source_url"), str)
    }
