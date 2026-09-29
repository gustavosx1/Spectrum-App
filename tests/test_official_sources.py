import pytest

from worker.utils import official_sources


def test_treasury_excerpt_uses_latest_published_months(monkeypatch):
    monkeypatch.setattr(
        official_sources,
        "_xlsx_rows",
        lambda _workbook: [
            {"B": "Mai/26", "C": "Jun/26", "D": "Jul/26"},
            {
                "A": "DPF EM PODER DO PÚBLICO",
                "B": "9032.65",
                "C": "9268.39",
                "D": "9288.78",
            },
        ],
    )

    excerpt = official_sources._treasury_dpf_excerpt(b"workbook")

    assert "Mai/26: 9032.65" in excerpt
    assert "Jul/26: 9288.78" in excerpt


def test_direct_evidence_urls_excludes_catalog_leads():
    evidence = [
        {
            "source_url": "https://api.bcb.gov.br/dados/serie/bcdata.sgs.432/dados/ultimos/6?formato=json",
            "kind": "direct_evidence",
        },
        {
            "source_url": "https://dadosabertos.tse.jus.br/dataset/candidatos-2024",
            "kind": "discovery_lead",
        },
    ]

    assert official_sources.direct_evidence_urls(evidence) == {
        "https://api.bcb.gov.br/dados/serie/bcdata.sgs.432/dados/ultimos/6?formato=json"
    }


def test_probable_sources_use_divulgacandcontas_for_candidate_records():
    sources = official_sources.probable_official_source_urls(
        "Candidatura de deputada foi registrada no TSE para as eleições de 2026."
    )

    assert sources == ["https://divulgacandcontas.tse.jus.br/divulga/"]


def test_probable_sources_use_pesqele_for_electoral_poll_records():
    sources = official_sources.probable_official_source_urls(
        "Pesquisa AtlasIntel registrada no TSE sob o código BR-04391/2026."
    )

    assert sources == [
        "https://pesqele-divulgacao.tse.jus.br/app/pesquisa/listar.xhtml"
    ]


def test_probable_sources_fall_back_to_fixed_government_portal():
    assert official_sources.probable_official_source_urls("Um fato sem assunto catalogado.") == []


@pytest.mark.asyncio
async def test_bcb_connector_returns_bounded_api_observations():
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return [
                {"data": "01/11/2026", "valor": "13.75"},
                {"data": "04/11/2026", "valor": "13.75"},
            ]

    class Client:
        async def get(self, _url, params=None):
            assert params == {"formato": "json"}
            return Response()

    evidence = await official_sources._bcb_series(Client(), {"selic"})

    assert evidence[0]["kind"] == "direct_evidence"
    assert evidence[0]["source_name"] == "Banco Central do Brasil"
    assert "04/11/2026: 13.75" in evidence[0]["excerpt"]