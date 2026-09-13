"""Évalue la transcription et la recherche sur un corpus réel.

Lancé par `make eval CORPUS=<nom>` (dans le conteneur `api`) :

1. charge et valide le corpus — un corpus mal formé échoue avant l'OCR ;
2. importe les pages et lance l'OCR par l'API, ou reprend un document existant
   (`--document`) ;
3. lit la dernière révision `ocr` de chaque page : on mesure la sortie du modèle,
   pas une correction humaine ;
4. construit un **index d'évaluation séparé** depuis ces révisions. L'index
   principal n'accepte que des révisions validées par un humain ; les faire
   valider par un script trahirait ce principe. Tout ce qui y entre existe en base ;
5. interroge : toutes les questions vectorisées, puis toutes les recherches, puis
   toutes les réponses — un seul échange de modèle (`OLLAMA_MAX_LOADED_MODELS=1`),
   pas deux par question ;
6. écrit le rapport, JSON et Markdown, dans `<corpus>/resultats/`.
"""

import argparse
import asyncio
import json
import logging
import mimetypes
import sys
import time
from dataclasses import asdict
from datetime import UTC, datetime
from itertools import batched
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
from elasticsearch import AsyncElasticsearch

from scriptoria.config import Settings, get_settings
from scriptoria.evaluation.corpus import Corpus, CorpusError, charger_corpus
from scriptoria.evaluation.questions import rang_premiere_page, reponse_contient
from scriptoria.evaluation.rapport import ResultatQuestion, construire_rapport, rapport_markdown
from scriptoria.evaluation.references import (
    RELECTURE,
    SAISIE,
    derniere_revision,
    fusionner_references,
)
from scriptoria.services.chunking import chunk_markdown
from scriptoria.services.embeddings import embed_texts
from scriptoria.services.generation import context_budget, generate_answer
from scriptoria.services.indexing import ensure_index, index_chunks, validate_embedding_dim
from scriptoria.services.preprocessing import PreprocessingOptions
from scriptoria.services.retrieval import build_answer_context, hybrid_search
from scriptoria.services.storage import MAX_PAGES_PER_DOCUMENT
from scriptoria.workers.arq_app import transcription_timeout_seconds
from scriptoria.workers.tasks import EMBEDDING_BATCH_SIZE

logger = logging.getLogger(__name__)

SCRUTATION_SECONDES = 10.0
ATTENTE_PRETRAITEMENT_SECONDES = 600.0
IMPORT_TIMEOUT_SECONDES = 300.0
TOP_K_RECHERCHE = 10
TOP_K_REPONSE = 5
STATUTS_TRANSCRITS = {"awaiting_validation", "validated", "indexed"}


class EvaluationError(RuntimeError):
    """Évaluation interrompue, avec la raison."""


# --- Document : import et OCR, par l'API ------------------------------------


def _verifier(response: httpx.Response, attendu: int, action: str) -> Any:
    if response.status_code != attendu:
        raise EvaluationError(f"{action} : HTTP {response.status_code} — {response.text}")
    return response.json() if response.content else None


def importer(api: httpx.Client, corpus: Corpus) -> str:
    if len(corpus.pages) > MAX_PAGES_PER_DOCUMENT:
        raise EvaluationError(
            f"{len(corpus.pages)} pages : l'API en accepte {MAX_PAGES_PER_DOCUMENT}"
        )
    fichiers = [
        (
            "files",
            (page.name, page.read_bytes(), mimetypes.guess_type(page.name)[0] or "image/jpeg"),
        )
        for page in corpus.pages
    ]
    document = _verifier(
        api.post("/documents", files=fichiers, timeout=IMPORT_TIMEOUT_SECONDES), 201, "import"
    )
    print(f"→ document {document['id']} importé ({len(corpus.pages)} pages)")
    return document["id"]


def attendre(
    api: httpx.Client, document_id: str, statuts: set[str], timeout: float
) -> dict[str, Any]:
    """Scrute le document jusqu'à l'un des statuts voulus, en affichant la progression."""
    echeance = time.monotonic() + timeout
    derniere_progression = None
    while time.monotonic() < echeance:
        document = _verifier(api.get(f"/documents/{document_id}"), 200, "lecture du document")
        if document["status"] in statuts:
            return document
        if document["status"] == "failed":
            raise EvaluationError(
                f"document {document_id} en échec "
                f"({document['pages_transcribed']} pages transcrites) : "
                f"relancer avec --document {document_id} pour reprendre"
            )
        progression = (document["status"], document["pages_transcribed"])
        if progression != derniere_progression:
            print(
                f"  … {document['status']} : "
                f"{document['pages_transcribed']}/{document['page_count']} pages"
            )
            derniere_progression = progression
        time.sleep(SCRUTATION_SECONDES)
    raise EvaluationError(f"statut {sorted(statuts)} non atteint en {timeout:.0f} s")


def transcrire(api: httpx.Client, document_id: str, settings: Settings) -> None:
    document = _verifier(api.get(f"/documents/{document_id}"), 200, "lecture du document")
    if document["status"] in STATUTS_TRANSCRITS:
        print(f"→ document déjà transcrit ({document['status']}) : OCR non relancé")
        return
    if document["status"] in {"new", "preprocessing"}:
        attendre(api, document_id, {"preprocessed", "failed"}, ATTENTE_PRETRAITEMENT_SECONDES)
        document = _verifier(api.get(f"/documents/{document_id}"), 200, "lecture du document")
    if document["status"] in {"preprocessed", "failed"}:
        _verifier(api.post(f"/documents/{document_id}/transcribe"), 202, "lancement de l'OCR")
        print("→ OCR en file")
    attendre(api, document_id, STATUTS_TRANSCRITS, transcription_timeout_seconds(settings))


def lire_revisions(api: httpx.Client, document_id: str) -> tuple[dict[int, str], dict[int, str]]:
    """Par page : la dernière sortie de l'OCR, et la dernière relecture validée.

    La première est ce qu'on mesure. La seconde sert de référence là où aucune page
    n'a été saisie en fichier : relire dans l'UI suffit à faire avancer la mesure.
    """
    pages = _verifier(api.get(f"/documents/{document_id}/pages"), 200, "lecture des pages")
    ocr: dict[int, str] = {}
    relectures: dict[int, str] = {}
    for page in pages:
        numero = page["page_number"]
        detail = _verifier(api.get(f"/pages/{page['id']}"), 200, f"lecture de la page {numero}")
        revisions = detail["transcriptions"]
        if (texte := derniere_revision(revisions, origine="ocr")) is not None:
            ocr[numero] = texte
        if (texte := derniere_revision(revisions, origine="human", validee=True)) is not None:
            relectures[numero] = texte
    return ocr, relectures


# --- Recherche : index d'évaluation séparé ----------------------------------


def nom_index_evaluation(settings: Settings, corpus: Corpus) -> str:
    return f"{settings.elasticsearch_index}-eval-{corpus.nom}"


async def indexer(
    es: AsyncElasticsearch,
    ollama: httpx.AsyncClient,
    settings: Settings,
    index: str,
    document_id: str,
    textes: dict[int, str],
) -> int:
    await es.indices.delete(index=index, ignore_unavailable=True)
    await ensure_index(es, index, settings.embedding_dim)
    fragments = [
        fragment
        for numero, texte in sorted(textes.items())
        for fragment in chunk_markdown(texte, UUID(document_id), numero)
    ]
    for lot in batched(fragments, EMBEDDING_BATCH_SIZE):
        vecteurs = await embed_texts(
            ollama, [f.content for f in lot], settings.ollama_embedding_model
        )
        validate_embedding_dim(vecteurs, settings.embedding_dim)
        await index_chunks(es, index, list(lot), vecteurs)
    await es.indices.refresh(index=index)
    return len(fragments)


async def interroger(
    corpus: Corpus,
    settings: Settings,
    document_id: str,
    textes: dict[int, str],
    avec_reponses: bool,
) -> tuple[list[ResultatQuestion], int]:
    es = AsyncElasticsearch(settings.elasticsearch_url)
    ollama = httpx.AsyncClient(
        base_url=settings.ollama_base_url, timeout=settings.ollama_timeout_seconds
    )
    index = nom_index_evaluation(settings, corpus)
    try:
        fragments = await indexer(es, ollama, settings, index, document_id, textes)
        print(f"→ {fragments} fragments dans l'index d'évaluation « {index} »")
        if not corpus.questions:
            return [], fragments

        vecteurs = await embed_texts(
            ollama, [q.question for q in corpus.questions], settings.ollama_embedding_model
        )
        passages = [
            await hybrid_search(es, index, q.question, vecteur, top_k=TOP_K_RECHERCHE)
            for q, vecteur in zip(corpus.questions, vecteurs, strict=True)
        ]

        reponses: list[str | None] = [None] * len(corpus.questions)
        if avec_reponses:
            for position, (question, trouves) in enumerate(
                zip(corpus.questions, passages, strict=True)
            ):
                if trouves:
                    num_ctx = settings.ollama_generation_num_ctx
                    num_predict = settings.ollama_generation_num_predict
                    contexte = build_answer_context(
                        trouves[:TOP_K_REPONSE],
                        max_chars=context_budget(question.question, num_ctx, num_predict),
                    )
                    reponses[position] = await generate_answer(
                        ollama,
                        question.question,
                        contexte["context"],
                        settings.ollama_generation_model,
                        num_ctx=num_ctx,
                        num_predict=num_predict,
                    )
                print(f"  … réponse {position + 1}/{len(corpus.questions)}")

        resultats = [
            ResultatQuestion(
                id=question.id,
                question=question.question,
                pages_attendues=question.pages,
                pages_retrouvees=tuple(p.page_number for p in trouves),
                rang=rang_premiere_page(
                    [{"page_number": p.page_number} for p in trouves], question.pages
                ),
                reponse=reponse,
                valeur_trouvee=reponse_contient(reponse, question.valeurs) if reponse else None,
            )
            for question, trouves, reponse in zip(corpus.questions, passages, reponses, strict=True)
        ]
        return resultats, fragments
    finally:
        await ollama.aclose()
        await es.close()


# --- Orchestration ----------------------------------------------------------


def configuration(
    settings: Settings, corpus: Corpus, document_id: str, horodatage: datetime
) -> dict[str, Any]:
    return {
        "date": horodatage.isoformat(timespec="seconds"),
        "document_id": document_id,
        "modele_vision": settings.ollama_vision_model,
        "modele_embeddings": settings.ollama_embedding_model,
        "modele_generation": settings.ollama_generation_model,
        "generation_num_ctx": settings.ollama_generation_num_ctx,
        "generation_num_predict": settings.ollama_generation_num_predict,
        "pretraitement": asdict(PreprocessingOptions()),
        "seuil_second_passage": settings.confidence_second_pass_threshold,
        "index_evaluation": nom_index_evaluation(settings, corpus),
        "top_k_recherche": TOP_K_RECHERCHE,
        "top_k_reponse": TOP_K_REPONSE,
    }


def resoudre_corpus(argument: str, settings: Settings) -> Path:
    chemin = Path(argument)
    return chemin if chemin.is_dir() else settings.data_dir / "corpus" / argument


def evaluer(argument: str, document_id: str | None, avec_reponses: bool) -> tuple[Path, Path, str]:
    settings = get_settings()
    corpus = charger_corpus(resoudre_corpus(argument, settings))
    print(
        f"Corpus « {corpus.nom} » : {len(corpus.pages)} pages, "
        f"{len(corpus.references)} référence(s) saisie(s), "
        f"{len(corpus.controles_tables) + len(corpus.controles_nombres)} contrôle(s), "
        f"{len(corpus.questions)} question(s)"
    )
    horodatage = datetime.now(UTC)
    durees: dict[str, float] = {}

    with httpx.Client(base_url=settings.api_base_url, timeout=60.0) as api:
        debut = time.monotonic()
        if document_id is None:
            document_id = importer(api, corpus)
        transcrire(api, document_id, settings)
        durees["import_et_ocr"] = time.monotonic() - debut
        textes, relectures = lire_revisions(api, document_id)

    references = fusionner_references(corpus.references, relectures)
    saisies = sum(1 for reference in references.values() if reference.origine == SAISIE)
    relues = sum(1 for reference in references.values() if reference.origine == RELECTURE)
    print(f"→ références : {saisies} saisie(s), {relues} relue(s) et validée(s) dans l'UI")

    debut = time.monotonic()
    questions, _ = asyncio.run(interroger(corpus, settings, document_id, textes, avec_reponses))
    durees["recherche"] = time.monotonic() - debut

    rapport = construire_rapport(
        corpus,
        textes,
        questions,
        configuration(settings, corpus, document_id, horodatage),
        durees,
        references=references,
    )
    markdown = rapport_markdown(rapport)
    resultats = corpus.dossier / "resultats"
    resultats.mkdir(exist_ok=True)
    base = resultats / f"{horodatage:%Y-%m-%dT%H%M%S}"
    base.with_suffix(".json").write_text(
        json.dumps(rapport, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    base.with_suffix(".md").write_text(markdown, encoding="utf-8")
    return base.with_suffix(".json"), base.with_suffix(".md"), markdown


def main() -> int:
    logging.basicConfig(level=logging.WARNING)
    parser = argparse.ArgumentParser(description="Évalue OCR et recherche sur un corpus réel.")
    parser.add_argument("corpus", help="nom du corpus sous DATA_DIR/corpus, ou chemin du dossier")
    parser.add_argument(
        "--document", help="reprendre un document déjà importé plutôt que réimporter"
    )
    parser.add_argument("--sans-reponses", action="store_true", help="ne pas générer de réponses")
    args = parser.parse_args()
    try:
        json_path, md_path, markdown = evaluer(args.corpus, args.document, not args.sans_reponses)
    except (CorpusError, EvaluationError) as exc:
        print(f"✗ évaluation interrompue : {exc}", file=sys.stderr)
        return 1
    # Rattrapé large : point d'entrée en ligne de commande, il doit rendre un
    # message lisible et un code de sortie, pas une trace brute.
    except Exception as exc:
        logger.exception("évaluation interrompue")
        print(f"✗ évaluation interrompue : {exc!r}", file=sys.stderr)
        return 1
    print("", markdown, f"✓ rapport écrit : {json_path} et {md_path}", sep="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
