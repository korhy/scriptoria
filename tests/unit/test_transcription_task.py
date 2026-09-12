"""Tâche OCR du worker.

Un lot de 200 pages se compte en heures : la propriété qui compte ici n'est pas
« la boucle transcrit », c'est **la reprise**. Un traitement interrompu à la
180ᵉ page doit repartir de la 181ᵉ, ce qui suppose d'enregistrer page par page
et de savoir reconnaître une page déjà transcrite.

La base et Ollama sont simulés : ces tests décrivent l'enchaînement, pas
SQLAlchemy. Le branchement réel est couvert par les tests d'intégration.
"""

from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest

from scriptoria.config import Settings
from scriptoria.db.models import Document, Job, Page, Transcription
from scriptoria.domain.enums import DocumentStatus, JobStatus, TranscriptionOrigin
from scriptoria.services.confidence import METHOD_ARITHMETIC, METHOD_DOUBLE_PASS
from scriptoria.services.ocr import OcrError, OcrResult
from scriptoria.workers.tasks import transcribe_document

MODELE = "qwen2.5vl:7b"


class FakeResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalars(self) -> "FakeResult":
        return self

    def all(self) -> list[Any]:
        return self._rows

    def scalar_one_or_none(self) -> Any:
        return self._rows[0] if self._rows else None


class FakeSession:
    """Session minimale, qui répond selon l'entité interrogée.

    `commits_par_ajout` retient combien de transcriptions étaient enregistrées à
    chaque validation : c'est ce qui permet de vérifier la reprise plutôt qu'un
    unique commit final.
    """

    def __init__(self, document: Document | None, pages: list[Page], job: Job | None) -> None:
        self.document = document
        self.pages = pages
        self.job = job
        self.added: list[Any] = []
        self.commits_par_ajout: list[int] = []

    async def __aenter__(self) -> "FakeSession":
        return self

    async def __aexit__(self, *exc_info: Any) -> bool:
        return False

    async def get(self, model: Any, ident: Any) -> Any:
        return self.document

    async def execute(self, statement: Any) -> FakeResult:
        entity = statement.column_descriptions[0]["entity"]
        if entity is Page:
            return FakeResult(self.pages)
        if entity is Job:
            return FakeResult([self.job] if self.job is not None else [])
        return FakeResult([])

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def commit(self) -> None:
        self.commits_par_ajout.append(len(self.transcriptions))

    @property
    def transcriptions(self) -> list[Transcription]:
        return [obj for obj in self.added if isinstance(obj, Transcription)]


def page(numero: int, document_id: UUID, *, pretraitee: bool = True) -> Page:
    return Page(
        id=uuid4(),
        document_id=document_id,
        page_number=numero,
        raw_image_path=f"inbox/{document_id}/{numero:04d}.png",
        preprocessed_image_path=f"images/{document_id}/{numero:04d}.png" if pretraitee else None,
    )


@pytest.fixture
def contexte(tmp_path: Path) -> dict[str, Any]:
    """Contexte arq minimal : mêmes clés que `arq_app.startup`."""
    document_id = uuid4()
    document = Document(
        id=document_id,
        source_filename="scan.png",
        status=DocumentStatus.PREPROCESSED,
        page_count=2,
    )
    session = FakeSession(
        document=document,
        pages=[page(1, document_id), page(2, document_id)],
        job=Job(document_id=document_id, kind="transcribe", status=JobStatus.QUEUED),
    )
    return {
        "settings": Settings(data_dir=tmp_path, ollama_vision_model=MODELE),
        "sessionmaker": lambda: session,
        "ollama": object(),
        "session": session,
        "document": document,
    }


@pytest.fixture
def transcriptions_simulees(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    """Remplace l'appel vision et retient les images qu'on lui a soumises."""
    vues: list[Path] = []

    async def faux_transcribe_page(client: Any, image_path: Path, model: str, **kwargs: Any):
        vues.append(image_path)
        return OcrResult(
            content_markdown=f"# page {image_path.stem}",
            model_name=model,
            duration_seconds=57.0,
        )

    monkeypatch.setattr("scriptoria.workers.tasks.transcribe_page", faux_transcribe_page)
    return vues


# --- Chemin nominal ---------------------------------------------------------


async def test_chaque_page_produit_une_revision_ocr(
    contexte: dict[str, Any], transcriptions_simulees: list[Path]
) -> None:
    await transcribe_document(contexte, str(contexte["document"].id))

    revisions = contexte["session"].transcriptions
    assert len(revisions) == 2
    assert all(revision.origin is TranscriptionOrigin.OCR for revision in revisions)
    assert all(revision.revision == 1 for revision in revisions)
    assert all(revision.model_name == MODELE for revision in revisions)


async def test_une_revision_ocr_n_est_jamais_validee_d_office(
    contexte: dict[str, Any], transcriptions_simulees: list[Path]
) -> None:
    """La validation est un geste humain : l'OCR ne peut pas se valider lui-même."""
    await transcribe_document(contexte, str(contexte["document"].id))

    assert all(not revision.is_validated for revision in contexte["session"].transcriptions)


async def test_le_document_finit_en_attente_de_validation(
    contexte: dict[str, Any], transcriptions_simulees: list[Path]
) -> None:
    await transcribe_document(contexte, str(contexte["document"].id))

    assert contexte["document"].status is DocumentStatus.AWAITING_VALIDATION


async def test_l_image_pretraitee_est_preferee_a_l_image_brute(
    contexte: dict[str, Any], transcriptions_simulees: list[Path]
) -> None:
    """La résolution d'entrée domine le coût : c'est l'image nettoyée qu'on soumet."""
    await transcribe_document(contexte, str(contexte["document"].id))

    assert all("images" in str(chemin) for chemin in transcriptions_simulees)


async def test_une_page_sans_image_pretraitee_retombe_sur_l_image_brute(
    contexte: dict[str, Any], transcriptions_simulees: list[Path]
) -> None:
    """Mieux vaut transcrire l'original que sauter la page en silence."""
    contexte["session"].pages = [page(1, contexte["document"].id, pretraitee=False)]

    await transcribe_document(contexte, str(contexte["document"].id))

    assert "inbox" in str(transcriptions_simulees[0])


# --- Reprise ----------------------------------------------------------------


async def test_chaque_page_est_enregistree_avant_de_passer_a_la_suivante(
    contexte: dict[str, Any], transcriptions_simulees: list[Path]
) -> None:
    """Sans validation page par page, une panne à la 180ᵉ page perd les 179 autres."""
    await transcribe_document(contexte, str(contexte["document"].id))

    # Une validation a eu lieu alors qu'une seule page était transcrite.
    assert 1 in contexte["session"].commits_par_ajout


async def test_une_page_deja_transcrite_n_est_pas_resoumise(
    contexte: dict[str, Any], transcriptions_simulees: list[Path]
) -> None:
    """Le cœur de la reprise : re-soumettre 179 pages coûterait trois heures."""
    deja_faite = contexte["session"].pages[0]
    deja_faite.transcriptions = [
        Transcription(
            page_id=deja_faite.id,
            revision=1,
            content_markdown="# déjà transcrite",
            origin=TranscriptionOrigin.OCR,
            model_name=MODELE,
        )
    ]

    await transcribe_document(contexte, str(contexte["document"].id))

    assert len(transcriptions_simulees) == 1
    assert transcriptions_simulees[0].stem == "0002"


async def test_une_page_seulement_corrigee_a_la_main_est_transcrite_en_revision_suivante(
    contexte: dict[str, Any], transcriptions_simulees: list[Path]
) -> None:
    """Une révision humaine n'est pas une transcription OCR : la numérotation suit."""
    cible = contexte["session"].pages[0]
    cible.transcriptions = [
        Transcription(
            page_id=cible.id,
            revision=1,
            content_markdown="saisie manuelle",
            origin=TranscriptionOrigin.HUMAN,
        )
    ]
    contexte["session"].pages = [cible]

    await transcribe_document(contexte, str(contexte["document"].id))

    assert contexte["session"].transcriptions[0].revision == 2


# --- Échecs -----------------------------------------------------------------


async def test_un_echec_marque_le_document_et_releve(
    contexte: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """arq doit voir l'échec : l'avaler ferait passer un document perdu pour traité."""

    async def echec(*args: Any, **kwargs: Any) -> OcrResult:
        raise OcrError("Ollama a répondu 500")

    monkeypatch.setattr("scriptoria.workers.tasks.transcribe_page", echec)

    with pytest.raises(OcrError):
        await transcribe_document(contexte, str(contexte["document"].id))

    assert contexte["document"].status is DocumentStatus.FAILED
    assert contexte["session"].job.status is JobStatus.FAILED
    assert "500" in (contexte["session"].job.error or "")


async def test_un_document_absent_n_est_pas_une_erreur(
    contexte: dict[str, Any], transcriptions_simulees: list[Path]
) -> None:
    """Supprimé entre la mise en file et l'exécution : à journaliser, pas à faire échouer."""
    contexte["session"].document = None

    await transcribe_document(contexte, str(uuid4()))

    assert transcriptions_simulees == []


async def test_l_absence_de_job_en_base_n_empeche_pas_la_transcription(
    contexte: dict[str, Any], transcriptions_simulees: list[Path]
) -> None:
    """Le suivi est un confort ; perdre la ligne de job ne doit pas perdre le document."""
    contexte["session"].job = None

    await transcribe_document(contexte, str(contexte["document"].id))

    assert len(contexte["session"].transcriptions) == 2


# --- Confiance --------------------------------------------------------------

# Une ligne arithmétiquement fausse : 6 x 28,60 = 171,60, pas 173,40.
PAGE_DOUTEUSE = (
    "| Désignation | Qté | PU HT | Total HT |\n"
    "|---|---|---|---|\n"
    "| Papier | 24 | 4,50 | 108,00 |\n"
    "| Encre | 6 | 28,60 | 173,40 |\n"
)


@pytest.fixture
def passages_simules(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Retient chaque appel vision, température comprise, et rend une page douteuse."""
    appels: list[dict[str, Any]] = []

    async def faux_transcribe_page(
        client: Any, image_path: Path, model: str, temperature: float = 0.0, **kwargs: Any
    ) -> OcrResult:
        appels.append({"image": image_path, "temperature": temperature})
        # Le second passage lit 28,90 là où le premier lisait 28,60 : divergence.
        markdown = PAGE_DOUTEUSE
        if temperature > 0:
            markdown = PAGE_DOUTEUSE.replace("28,60", "28,90")
        return OcrResult(content_markdown=markdown, model_name=model, duration_seconds=57.0)

    monkeypatch.setattr("scriptoria.workers.tasks.transcribe_page", faux_transcribe_page)
    return appels


async def test_les_blocs_de_confiance_sont_enregistres_avec_la_revision(
    contexte: dict[str, Any], passages_simules: list[dict[str, Any]]
) -> None:
    """Sans blocs en base, l'UI de validation n'a rien à surligner."""
    contexte["session"].pages = [page(1, contexte["document"].id)]

    await transcribe_document(contexte, str(contexte["document"].id))

    revision = contexte["session"].transcriptions[0]
    assert revision.confidence_blocks, "aucun bloc de confiance enregistré"
    assert METHOD_ARITHMETIC in {bloc.method for bloc in revision.confidence_blocks}


async def test_une_page_propre_ne_coute_qu_un_passage(
    contexte: dict[str, Any], transcriptions_simulees: list[Path]
) -> None:
    """Le double passage systématique coûterait 6 h sur 200 pages au lieu de 3."""
    await transcribe_document(contexte, str(contexte["document"].id))

    assert len(transcriptions_simulees) == 2, "un passage par page, pas deux"


async def test_une_page_douteuse_declenche_un_second_passage(
    contexte: dict[str, Any], passages_simules: list[dict[str, Any]]
) -> None:
    """C'est là que la dépense se justifie : sur les pages qui échouent aux contrôles."""
    contexte["session"].pages = [page(1, contexte["document"].id)]

    await transcribe_document(contexte, str(contexte["document"].id))

    assert len(passages_simules) == 2
    # À température nulle, le modèle redonnerait mot pour mot la même sortie :
    # la divergence ne mesurerait rien.
    assert passages_simules[1]["temperature"] > 0


async def test_la_divergence_entre_passages_est_enregistree(
    contexte: dict[str, Any], passages_simules: list[dict[str, Any]]
) -> None:
    contexte["session"].pages = [page(1, contexte["document"].id)]

    await transcribe_document(contexte, str(contexte["document"].id))

    revision = contexte["session"].transcriptions[0]
    assert METHOD_DOUBLE_PASS in {bloc.method for bloc in revision.confidence_blocks}


async def test_la_revision_conservee_est_celle_du_premier_passage(
    contexte: dict[str, Any], passages_simules: list[dict[str, Any]]
) -> None:
    """Le premier passage est à température nulle : c'est le plus fidèle des deux."""
    contexte["session"].pages = [page(1, contexte["document"].id)]

    await transcribe_document(contexte, str(contexte["document"].id))

    assert "28,60" in contexte["session"].transcriptions[0].content_markdown


async def test_un_seuil_a_zero_desactive_le_second_passage(
    contexte: dict[str, Any], passages_simules: list[dict[str, Any]], tmp_path: Path
) -> None:
    """Levier d'exploitation : un lot urgent doit pouvoir renoncer au second passage."""
    contexte["settings"] = Settings(
        data_dir=tmp_path,
        ollama_vision_model=MODELE,
        confidence_second_pass_threshold=0.0,
    )
    contexte["session"].pages = [page(1, contexte["document"].id)]

    await transcribe_document(contexte, str(contexte["document"].id))

    assert len(passages_simules) == 1
