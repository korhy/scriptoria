"""Configuration.

Un point mérite un test explicite : la contrainte « 100 % local » du projet
repose sur le fait qu'aucune URL ne pointe hors de la machine.
"""

from pathlib import Path

from scriptoria.config import Settings


def test_aucune_dependance_ne_sort_de_la_machine() -> None:
    """Garde-fou sur l'invariant fondateur : aucun appel API externe."""
    settings = Settings()

    hotes_locaux = ("localhost", "127.0.0.1", "host.docker.internal")
    reseau_docker = ("postgres", "redis", "elasticsearch", "api")

    for url in (
        settings.database_url,
        settings.redis_url,
        settings.elasticsearch_url,
        settings.ollama_base_url,
        settings.api_base_url,
    ):
        assert any(hote in url for hote in hotes_locaux + reseau_docker), (
            f"{url} ne pointe pas vers une ressource locale"
        )


def test_les_repertoires_derivent_de_data_dir(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path)

    assert settings.inbox_dir == tmp_path / "inbox"
    assert settings.images_dir == tmp_path / "images"
    assert settings.markdown_dir == tmp_path / "markdown"


def test_dimension_embedding_alignee_sur_bge_m3() -> None:
    """Un écart avec le mapping dense_vector fait rejeter l'indexation par ES."""
    assert Settings().embedding_dim == 1024
