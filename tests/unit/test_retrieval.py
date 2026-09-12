"""Recherche hybride et fusion des classements (RRF).

La fusion côté Python n'est pas un plan de repli : c'est le chemin réel. Le
`retriever: {rrf}` natif d'Elasticsearch est refusé par la licence basic
(vérifié le 2026-09-11 sur ES 9.1.0), d'où deux requêtes fusionnées ici.
"""

import pytest

from scriptoria.services.retrieval import (
    RetrievedChunk,
    build_answer_context,
    hybrid_search,
    reciprocal_rank_fusion,
)


def test_document_present_dans_les_deux_classements_passe_devant() -> None:
    """Un résultat trouvé à la fois par BM25 et par similarité doit primer."""
    bm25 = ["a", "b", "c"]
    dense = ["c", "a", "d"]

    scores = reciprocal_rank_fusion([bm25, dense])

    assert max(scores, key=lambda key: scores[key]) == "a"


def test_le_score_ne_depend_que_du_rang() -> None:
    """RRF ignore l'échelle des scores sources — tout l'intérêt ici, BM25 et
    cosinus n'ayant aucune échelle commune."""
    scores = reciprocal_rank_fusion([["x", "y"]])

    assert scores["x"] == 1 / 61
    assert scores["y"] == 1 / 62


def test_classements_vides_ne_produisent_aucun_score() -> None:
    assert reciprocal_rank_fusion([]) == {}
    assert reciprocal_rank_fusion([[], []]) == {}


def test_k_amortit_l_ecart_entre_les_rangs() -> None:
    """Un k plus grand rapproche les scores : comportement attendu de la formule."""
    serre = reciprocal_rank_fusion([["x", "y"]], k=1000)
    large = reciprocal_rank_fusion([["x", "y"]], k=1)

    assert serre["x"] - serre["y"] < large["x"] - large["y"]


# --- Recherche hybride ------------------------------------------------------


class FakeEs:
    """Elasticsearch simulé : retient chaque requête reçue.

    Ce qu'on vérifie surtout ici, c'est la **forme** des requêtes : deux appels
    distincts, et jamais de `retriever` RRF natif — refusé par la licence basic.
    """

    def __init__(self, bm25: list[dict] | None = None, knn: list[dict] | None = None) -> None:
        self.bm25 = bm25 if bm25 is not None else []
        self.knn = knn if knn is not None else []
        self.calls: list[dict] = []

    async def search(self, **kwargs: object) -> dict:
        self.calls.append(kwargs)
        hits = self.knn if "knn" in kwargs else self.bm25
        return {"hits": {"hits": hits}}


def hit(chunk_id: str, page: int = 1, content: str = "contenu", score: float = 1.0) -> dict:
    return {
        "_id": chunk_id,
        "_score": score,
        "_source": {
            "document_id": "b522c30f-a742-4f09-a793-9b0f12bf8d23",
            "page_number": page,
            "content": content,
        },
    }


async def test_deux_requetes_sont_emises_et_aucun_retriever_rrf() -> None:
    """Vérifié le 2026-09-11 : le `retriever: {rrf}` natif est refusé en licence basic.

    La fusion se fait côté Python. Ce test est le garde-fou contre la tentation
    de revenir à un retriever qui échouerait à l'exécution.
    """
    es = FakeEs(bm25=[hit("a")], knn=[hit("a")])

    await hybrid_search(es, "scriptoria-chunks", "encre", [0.1] * 4, top_k=3)

    assert len(es.calls) == 2
    lexicale, semantique = es.calls
    assert "query" in lexicale and "knn" not in lexicale
    assert "knn" in semantique
    assert all("retriever" not in appel for appel in es.calls)


async def test_un_fragment_trouve_par_les_deux_strategies_passe_devant() -> None:
    es = FakeEs(
        bm25=[hit("doc:1"), hit("doc:2", page=2)],
        knn=[hit("doc:3", page=3), hit("doc:1")],
    )

    resultats = await hybrid_search(es, "index", "encre", [0.1] * 4, top_k=3)

    assert resultats[0].chunk_id == "doc:1"


async def test_le_nombre_de_resultats_est_borne() -> None:
    es = FakeEs(
        bm25=[hit(f"doc:{numero}", page=numero) for numero in range(1, 6)],
        knn=[hit(f"doc:{numero}", page=numero) for numero in range(6, 11)],
    )

    resultats = await hybrid_search(es, "index", "encre", [0.1] * 4, top_k=4)

    assert len(resultats) == 4


async def test_le_contenu_et_la_page_accompagnent_chaque_resultat() -> None:
    """Sans la page, une réponse générée serait invérifiable par le lecteur."""
    es = FakeEs(bm25=[hit("doc:7", page=7, content="| Encre | 6 | 28,90 |")])

    resultat = (await hybrid_search(es, "index", "encre", [0.1] * 4, top_k=3))[0]

    assert resultat.page_number == 7
    assert resultat.content == "| Encre | 6 | 28,90 |"
    assert resultat.document_id == "b522c30f-a742-4f09-a793-9b0f12bf8d23"


async def test_un_index_sans_resultat_ne_renvoie_rien() -> None:
    es = FakeEs()

    assert await hybrid_search(es, "index", "encre", [0.1] * 4, top_k=3) == []


async def test_le_score_rendu_est_celui_de_la_fusion() -> None:
    """Les scores BM25 et cosinus n'ont aucune échelle commune : seul le rang compte."""
    es = FakeEs(bm25=[hit("doc:1", score=42.0)], knn=[hit("doc:1", score=0.87)])

    resultat = (await hybrid_search(es, "index", "encre", [0.1] * 4, top_k=3))[0]

    assert resultat.score == pytest.approx(2 / 61)


# --- Contexte de génération -------------------------------------------------


def test_le_contexte_cite_la_page_de_chaque_passage() -> None:
    chunks = [
        RetrievedChunk(
            chunk_id="doc:1",
            document_id="b522c30f-a742-4f09-a793-9b0f12bf8d23",
            page_number=1,
            content="Total HT | 311,40",
            score=0.5,
        )
    ]

    contexte = build_answer_context(chunks)

    assert "page 1" in contexte["context"]
    assert "Total HT | 311,40" in contexte["context"]
    assert contexte["sources"] == chunks


def test_un_contexte_sans_passage_est_vide_et_le_dit() -> None:
    """Mieux vaut un contexte vide qu'un contexte inventé pour faire nombre."""
    contexte = build_answer_context([])

    assert contexte["context"] == ""
    assert contexte["sources"] == []
