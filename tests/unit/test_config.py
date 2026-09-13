"""Configuration.

Un point mérite un test explicite : la contrainte « 100 % local » du projet
repose sur le fait qu'aucune URL ne pointe hors de la machine.
"""

import tomllib
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


def test_l_ui_n_envoie_aucune_statistique_d_usage() -> None:
    """Streamlit envoie par défaut des statistiques d'usage vers un service tiers.

    Constaté le 2026-09-13 dans le navigateur : des POST vers
    `webhooks.fivetran.com` à chaque interaction. Le garde-fou ci-dessus ne voit
    que la configuration de l'application ; celui-ci couvre l'UI.
    """
    config = Path(__file__).parents[2] / "src" / "scriptoria" / "ui" / ".streamlit" / "config.toml"

    assert config.is_file(), f"{config} absent : Streamlit garderait ses réglages par défaut"
    reglages = tomllib.loads(config.read_text())
    assert reglages["browser"]["gatherUsageStats"] is False


def test_l_ui_ne_cherche_pas_son_adresse_publique() -> None:
    """Au démarrage, Streamlit interroge `checkip.amazonaws.com` pour afficher une URL externe.

    Constaté le 2026-09-13 dans les logs du conteneur (`External URL: …`). L'appel
    est évité quand `browser.serverAddress` est fixé : Streamlit n'a alors plus
    d'adresse à deviner.
    """
    config = Path(__file__).parents[2] / "src" / "scriptoria" / "ui" / ".streamlit" / "config.toml"

    assert tomllib.loads(config.read_text())["browser"].get("serverAddress") == "localhost"
