"""Traitements asynchrones.

L'OCR d'une page dépasse largement la durée acceptable d'une requête HTTP :
il ne doit jamais s'exécuter dans le cycle de l'API.

Les noms de tâches vivent ici et non dans les routers : l'API les enfile, le
worker les exécute, et une chaîne recopiée des deux côtés finit par diverger
sans que rien ne le signale — le job partirait dans le vide.
"""

PREPROCESS_TASK = "preprocess_document"
TRANSCRIBE_TASK = "transcribe_document"
INDEX_TASK = "index_document"
