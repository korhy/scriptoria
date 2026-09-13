"""Chargement d'un corpus d'évaluation.

Structure d'un corpus (hors Git, sous `data/corpus/<nom>/`) :

    pages/page-NN.jpg        une image par page, numérotées sans trou
    reference/page-NN.md     texte de référence saisi à la main (facultatif)
    controles.toml           contrôles objectifs (facultatif)
    questions.toml           questions de recherche (facultatif)

Un corpus mal formé échoue au chargement, en disant pourquoi : le découvrir
après quarante minutes d'OCR fausserait tout le passage.
"""

import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

_PAGE = re.compile(r"^page-(\d+)\.(?:jpe?g|png|tiff?|webp|bmp)$", re.IGNORECASE)
_REFERENCE = re.compile(r"^page-(\d+)\.md$")


class CorpusError(ValueError):
    """Corpus inutilisable, avec la raison."""


@dataclass(frozen=True)
class TableAttendue:
    nom: str
    lots: int
    total: int


@dataclass(frozen=True)
class ControleTables:
    nom: str
    premiere_page: int
    derniere_page: int
    denominateur: int
    tables: tuple[TableAttendue, ...]


@dataclass(frozen=True)
class ControleNombres:
    nom: str
    page: int
    nombres: tuple[str, ...]


@dataclass(frozen=True)
class Question:
    id: str
    question: str
    pages: tuple[int, ...]
    valeurs: tuple[str, ...]


@dataclass(frozen=True)
class Corpus:
    nom: str
    dossier: Path
    pages: tuple[Path, ...]
    references: Mapping[int, str]
    controles_tables: tuple[ControleTables, ...]
    controles_nombres: tuple[ControleNombres, ...]
    questions: tuple[Question, ...]


def _charger_pages(dossier: Path) -> tuple[Path, ...]:
    par_numero: dict[int, Path] = {}
    repertoire = dossier / "pages"
    fichiers = sorted(repertoire.iterdir()) if repertoire.is_dir() else []
    for fichier in fichiers:
        if match := _PAGE.match(fichier.name):
            numero = int(match.group(1))
            if numero in par_numero:
                raise CorpusError(
                    f"page {numero} en double : {par_numero[numero].name}, {fichier.name}"
                )
            par_numero[numero] = fichier

    if not par_numero:
        raise CorpusError(f"aucune page dans {repertoire} (attendu : pages/page-01.jpg, …)")
    for numero in range(1, max(par_numero) + 1):
        if numero not in par_numero:
            raise CorpusError(
                f"page {numero} absente de {repertoire} : les pages doivent se suivre"
            )
    return tuple(par_numero[numero] for numero in sorted(par_numero))


def _verifier_page(page: int, nombre_pages: int, origine: str) -> None:
    if not 1 <= page <= nombre_pages:
        raise CorpusError(f"{origine} : page {page} hors du corpus (pages 1 à {nombre_pages})")


def _charger_references(dossier: Path, nombre_pages: int) -> Mapping[int, str]:
    references: dict[int, str] = {}
    repertoire = dossier / "reference"
    if not repertoire.is_dir():
        return MappingProxyType(references)
    for fichier in sorted(repertoire.iterdir()):
        if match := _REFERENCE.match(fichier.name):
            texte = fichier.read_text(encoding="utf-8")
            if not texte.strip():
                raise CorpusError(
                    f"{fichier.name} est vide : ne créer le fichier qu'une fois la page saisie"
                )
            numero = int(match.group(1))
            _verifier_page(numero, nombre_pages, f"référence {fichier.name}")
            references[numero] = texte
    return MappingProxyType(references)


def _lire_toml(chemin: Path) -> dict[str, Any]:
    if not chemin.is_file():
        return {}
    try:
        return tomllib.loads(chemin.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise CorpusError(f"{chemin.name} illisible : {exc}") from exc


def _charger_controles(
    dossier: Path, nombre_pages: int
) -> tuple[tuple[ControleTables, ...], tuple[ControleNombres, ...]]:
    donnees = _lire_toml(dossier / "controles.toml")
    try:
        tables = tuple(
            ControleTables(
                nom=entree["nom"],
                premiere_page=entree["pages"][0],
                derniere_page=entree["pages"][1],
                denominateur=entree["denominateur"],
                tables=tuple(
                    TableAttendue(t["nom"], t["lots"], t["total"]) for t in entree["lots"]
                ),
            )
            for entree in donnees.get("tables", [])
        )
        nombres = tuple(
            ControleNombres(
                nom=entree["nom"], page=entree["page"], nombres=tuple(entree["nombres"])
            )
            for entree in donnees.get("nombres", [])
        )
    except (KeyError, IndexError, TypeError) as exc:
        raise CorpusError(f"controles.toml : entrée incomplète ({exc!r})") from exc

    for table in tables:
        for page in (table.premiere_page, table.derniere_page):
            _verifier_page(page, nombre_pages, f"contrôle « {table.nom} »")
    for controle in nombres:
        _verifier_page(controle.page, nombre_pages, f"contrôle « {controle.nom} »")
    return tables, nombres


def _charger_questions(dossier: Path, nombre_pages: int) -> tuple[Question, ...]:
    donnees = _lire_toml(dossier / "questions.toml")
    try:
        questions = tuple(
            Question(
                id=entree["id"],
                question=entree["question"],
                pages=tuple(entree["pages"]),
                valeurs=tuple(entree["valeurs"]),
            )
            for entree in donnees.get("questions", [])
        )
    except (KeyError, TypeError) as exc:
        raise CorpusError(f"questions.toml : entrée incomplète ({exc!r})") from exc

    for question in questions:
        for page in question.pages:
            _verifier_page(page, nombre_pages, f"question « {question.id} »")
    return questions


def charger_corpus(dossier: Path) -> Corpus:
    pages = _charger_pages(dossier)
    tables, nombres = _charger_controles(dossier, len(pages))
    return Corpus(
        nom=dossier.name,
        dossier=dossier,
        pages=pages,
        references=_charger_references(dossier, len(pages)),
        controles_tables=tables,
        controles_nombres=nombres,
        questions=_charger_questions(dossier, len(pages)),
    )
