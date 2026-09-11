"""Logique métier du pipeline.

Chaque module est une étape isolée du pipeline. Les signatures sont figées, les
implémentations ne le sont pas : c'est délibéré, plusieurs choix techniques
restent ouverts (méthode de confiance, granularité du chunking) et doivent
pouvoir être tranchés par l'expérimentation sans réécrire les frontières.
"""
