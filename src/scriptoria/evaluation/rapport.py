"""Construction du rapport d'évaluation.

Le rapport distingue trois états, et ne les confond jamais :

- **juste** ou **faux** : la mesure a eu lieu ;
- **non mesuré** : faute de référence, de page transcrite ou de question. Un taux
  d'erreur à 0 faute de référence ferait prendre une absence de mesure pour un
  excellent résultat.

Fonctions pures : aucune ne touche à la stack, ce qui permet de les tester.
"""

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any

from scriptoria.evaluation.controles import verifier_nombres_attendus, verifier_tables
from scriptoria.evaluation.corpus import Corpus
from scriptoria.evaluation.mesures import (
    ScoreNombres,
    exactitude_nombres,
    normaliser_mise_en_page,
    taux_erreur_caracteres,
)
from scriptoria.evaluation.questions import rappel_a_k

RANGS_RAPPORTES = (1, 3, 5, 10)
NON_MESURE = "non mesuré"


@dataclass(frozen=True)
class ResultatQuestion:
    id: str
    question: str
    pages_attendues: tuple[int, ...]
    pages_retrouvees: tuple[int, ...]
    rang: int | None
    reponse: str | None
    valeur_trouvee: str | None


def _score(score: ScoreNombres) -> dict[str, Any]:
    return {
        "attendus": score.attendus,
        "lus": score.lus,
        "justes": score.justes,
        "rappel": score.rappel,
        "precision": score.precision,
    }


def evaluer_transcriptions(corpus: Corpus, textes: Mapping[int, str]) -> dict[str, Any]:
    """Taux d'erreur et exactitude des nombres, sur les pages dotées d'une référence.

    Le taux global est pondéré par la longueur : une page courte parfaite ne doit
    pas masquer une page longue ratée.
    """
    pages: list[dict[str, Any]] = []
    sans_transcription: list[int] = []
    caracteres = 0
    erreurs = 0.0
    total = ScoreNombres(0, 0, 0)

    for numero, reference in sorted(corpus.references.items()):
        if numero not in textes:
            sans_transcription.append(numero)
            continue
        cer = taux_erreur_caracteres(reference, textes[numero])
        longueur = len(normaliser_mise_en_page(reference))
        score = exactitude_nombres(reference, textes[numero])
        pages.append({"page": numero, "cer": cer, "caracteres": longueur, "nombres": _score(score)})
        caracteres += longueur
        erreurs += cer * longueur
        total = ScoreNombres(
            total.attendus + score.attendus, total.lus + score.lus, total.justes + score.justes
        )

    return {
        "cer_global": erreurs / caracteres if caracteres else None,
        "pages": pages,
        "references_sans_transcription": sans_transcription,
        "nombres": _score(total),
    }


def evaluer_controles(corpus: Corpus, textes: Mapping[int, str]) -> dict[str, Any]:
    tables = [
        {"controle": controle.nom, **asdict(resultat), "juste": resultat.juste}
        for controle in corpus.controles_tables
        for resultat in verifier_tables(textes, controle)
    ]
    nombres = [
        {**asdict(resultat), "juste": resultat.juste}
        for resultat in (verifier_nombres_attendus(textes, c) for c in corpus.controles_nombres)
    ]
    resultats = tables + nombres
    return {
        "tables": tables,
        "nombres": nombres,
        "justes": sum(1 for resultat in resultats if resultat["juste"]),
        "total": len(resultats),
    }


def evaluer_recherche(questions: Sequence[ResultatQuestion]) -> dict[str, Any] | None:
    if not questions:
        return None
    rangs = [question.rang for question in questions]
    avec_reponse = [question for question in questions if question.reponse is not None]
    return {
        "questions": len(questions),
        **{f"rappel_a_{k}": rappel_a_k(rangs, k) for k in RANGS_RAPPORTES},
        "reponses_exactes": (
            sum(1 for q in avec_reponse if q.valeur_trouvee is not None) / len(avec_reponse)
            if avec_reponse
            else None
        ),
        "detail": [asdict(question) for question in questions],
    }


def construire_rapport(
    corpus: Corpus,
    textes: Mapping[int, str],
    questions: Sequence[ResultatQuestion],
    configuration: Mapping[str, Any],
    durees: Mapping[str, float],
) -> dict[str, Any]:
    return {
        "corpus": corpus.nom,
        "pages": len(corpus.pages),
        "pages_non_transcrites": [n for n in range(1, len(corpus.pages) + 1) if n not in textes],
        "configuration": dict(configuration),
        "durees_secondes": dict(durees),
        "transcription": evaluer_transcriptions(corpus, textes),
        "controles": evaluer_controles(corpus, textes),
        "recherche": evaluer_recherche(questions),
    }


def _pourcentage(valeur: float | None) -> str:
    return NON_MESURE if valeur is None else f"{valeur:.1%}"


def _marque(juste: bool) -> str:
    return "✓" if juste else "✗"


def _section_transcription(transcription: Mapping[str, Any]) -> list[str]:
    lignes = ["## Transcription", ""]
    if transcription["cer_global"] is None:
        lignes.append(
            f"- Taux d'erreur par caractère : {NON_MESURE} (aucun texte de référence saisi)"
        )
    else:
        lignes.append(
            f"- Taux d'erreur par caractère : {_pourcentage(transcription['cer_global'])} "
            f"sur {len(transcription['pages'])} page(s) de référence"
        )
    nombres = transcription["nombres"]
    lignes.append(
        f"- Nombres : {nombres['justes']} justes sur {nombres['attendus']} attendus "
        f"(rappel {_pourcentage(nombres['rappel'])}, "
        f"précision {_pourcentage(nombres['precision'])})"
    )
    if transcription["references_sans_transcription"]:
        lignes.append(
            f"- Références sans transcription : {transcription['references_sans_transcription']}"
        )
    if transcription["pages"]:
        lignes += ["", "| Page | Taux d'erreur | Nombres justes |", "|---|---|---|"]
        lignes += [
            f"| {p['page']} | {_pourcentage(p['cer'])} | "
            f"{p['nombres']['justes']}/{p['nombres']['attendus']} |"
            for p in transcription["pages"]
        ]
    return [*lignes, ""]


def _section_controles(controles: Mapping[str, Any]) -> list[str]:
    lignes = [f"## Contrôles objectifs ({controles['justes']}/{controles['total']})", ""]
    for table in controles["tables"]:
        manquantes = (
            f", pages non transcrites {list(table['pages_manquantes'])}"
            if table["pages_manquantes"]
            else ""
        )
        lignes.append(
            f"- {_marque(table['juste'])} {table['controle']} — {table['nom']} : "
            f"{table['lots_lus']}/{table['lots_attendus']} lots, "
            f"somme {table['somme']}/{table['total_attendu']}, "
            f"ligne de total {'lue' if table['total_lu'] else 'absente'}{manquantes}"
        )
    for nombres in controles["nombres"]:
        detail = (
            "page non transcrite"
            if nombres["page_manquante"]
            else f"manquants {list(nombres['manquants'])}"
        )
        lignes.append(
            f"- {_marque(nombres['juste'])} {nombres['nom']} (page {nombres['page']}) : "
            f"{len(nombres['trouves'])} trouvés, {detail}"
        )
    return [*lignes, ""]


def _section_recherche(recherche: Mapping[str, Any] | None) -> list[str]:
    lignes = ["## Recherche", ""]
    if recherche is None:
        return [*lignes, f"- {NON_MESURE} (aucune question)", ""]
    rappels = ", ".join(f"@{k} {_pourcentage(recherche[f'rappel_a_{k}'])}" for k in RANGS_RAPPORTES)
    lignes.append(f"- Rappel ({recherche['questions']} questions) : {rappels}")
    lignes.append(f"- Réponses exactes : {_pourcentage(recherche['reponses_exactes'])}")
    lignes += [
        "",
        "| | Question | Rang | Pages retrouvées | Valeur trouvée |",
        "|---|---|---|---|---|",
    ]
    for q in recherche["detail"]:
        juste = q["rang"] is not None and (q["reponse"] is None or q["valeur_trouvee"] is not None)
        lignes.append(
            f"| {_marque(juste)} | {q['id']} | {q['rang'] or '—'} | "
            f"{list(q['pages_retrouvees'])[:5]} | {q['valeur_trouvee'] or '—'} |"
        )
    return [*lignes, ""]


def rapport_markdown(rapport: Mapping[str, Any]) -> str:
    lignes = [f"# Évaluation — {rapport['corpus']}", ""]
    lignes += [f"- {cle} : {valeur}" for cle, valeur in rapport["configuration"].items()]
    non_transcrites = rapport["pages_non_transcrites"]
    lignes.append(
        f"- Pages transcrites : {rapport['pages'] - len(non_transcrites)}/{rapport['pages']}"
        + (f" (non transcrites : {non_transcrites})" if non_transcrites else "")
    )
    lignes += [
        f"- Durée {etape} : {secondes:.0f} s"
        for etape, secondes in rapport["durees_secondes"].items()
    ]
    lignes.append("")
    lignes += _section_transcription(rapport["transcription"])
    lignes += _section_controles(rapport["controles"])
    lignes += _section_recherche(rapport["recherche"])
    return "\n".join(lignes)
