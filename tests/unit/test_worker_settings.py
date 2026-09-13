"""Réglages du worker arq.

Le délai d'une tâche n'est pas un détail de configuration : arq **annule** une
tâche qui le dépasse. Le 2026-09-13, `job_timeout = 3600` coupait tout lot de
plus de ~60 pages, alors qu'un document en admet 200 à ~35-57 s chacune.
"""

from arq.worker import Function

from scriptoria.config import Settings, get_settings
from scriptoria.services.storage import MAX_PAGES_PER_DOCUMENT
from scriptoria.workers.arq_app import WorkerSettings, transcription_timeout_seconds


def fonction(nom: str) -> Function | None:
    """La déclaration arq d'une tâche, si elle porte des réglages propres."""
    for declaration in WorkerSettings.functions:
        if isinstance(declaration, Function) and declaration.name == nom:
            return declaration
    return None


def test_le_delai_de_l_ocr_couvre_un_lot_complet_au_pire_cas() -> None:
    """Chaque page peut consommer deux délais Ollama complets (double passage).

    Une page bloquée est déjà coupée par le délai HTTP : ce délai-ci n'est qu'un
    filet, il ne doit jamais interrompre un lot qui avance.
    """
    settings = Settings(ollama_timeout_seconds=300.0)

    assert transcription_timeout_seconds(settings) >= MAX_PAGES_PER_DOCUMENT * 2 * 300.0


def test_le_worker_applique_ce_delai_a_l_ocr() -> None:
    transcription = fonction("transcribe_document")

    assert transcription is not None
    assert transcription.timeout_s == transcription_timeout_seconds(get_settings())


def test_les_autres_taches_gardent_le_delai_par_defaut() -> None:
    """Prétraiter ou indexer 200 pages tient en minutes : une heure reste un filet sain."""
    assert WorkerSettings.job_timeout == 3600
    for nom in ("preprocess_document", "index_document"):
        declaration = fonction(nom)
        assert declaration is None or declaration.timeout_s is None
