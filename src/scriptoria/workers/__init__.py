"""Traitements asynchrones.

L'OCR d'une page dépasse largement la durée acceptable d'une requête HTTP :
il ne doit jamais s'exécuter dans le cycle de l'API.
"""
