"""Fusion des classements (RRF).

Seule brique de recherche déjà implémentée : c'est le plan de repli si la fusion
native d'Elasticsearch n'est pas disponible sous la licence en place.
"""

from scriptoria.services.retrieval import reciprocal_rank_fusion


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
