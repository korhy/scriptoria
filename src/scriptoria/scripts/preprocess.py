"""Prétraite une image de page et rapporte ce qui a été fait.

Permet de vérifier le prétraitement sur un fichier réel sans attendre que la
route d'import et la tâche worker existent. Sert aussi à comparer des réglages
sur un même document :

    python -m scriptoria.scripts.preprocess in.png out.png --max-edge 900
    python -m scriptoria.scripts.preprocess in.png out.png --contrast none --no-deskew
"""

import argparse
import sys
from pathlib import Path

from scriptoria.services.preprocessing import PreprocessingOptions, preprocess_page


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m scriptoria.scripts.preprocess",
        description="Prétraite une image de page (gris, débruitage, redressement, contraste).",
    )
    parser.add_argument("source", type=Path, help="image brute")
    parser.add_argument("destination", type=Path, help="image nettoyée à produire")
    parser.add_argument(
        "--max-edge",
        type=int,
        default=PreprocessingOptions.max_edge_px,
        help=(
            "plus grand côté en pixels (défaut: %(default)s). "
            "Principal levier du coût OCR : l'encodage de l'image représente "
            "l'essentiel du temps par page."
        ),
    )
    parser.add_argument(
        "--contrast",
        choices=["none", "clahe", "adaptive_threshold"],
        default=PreprocessingOptions.contrast,
        help=(
            "mode de contraste (défaut: %(default)s). "
            "adaptive_threshold binarise : probablement nuisible à un LLM vision."
        ),
    )
    parser.add_argument("--no-denoise", action="store_true", help="désactive le débruitage")
    parser.add_argument("--no-deskew", action="store_true", help="désactive le redressement")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    options = PreprocessingOptions(
        max_edge_px=args.max_edge,
        denoise=not args.no_denoise,
        deskew=not args.no_deskew,
        contrast=args.contrast,
    )

    try:
        result = preprocess_page(args.source, args.destination, options)
    except (ValueError, OSError) as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 1

    source_width, source_height = result.source_size
    output_width, output_height = result.output_size
    pixel_ratio = (output_width * output_height) / (source_width * source_height)

    print(f"✓ {result.output_path}")
    print(f"  dimensions  : {source_width}x{source_height} → {output_width}x{output_height}")
    # Le coût d'encodage suit le nombre de pixels, pas le plus grand côté.
    print(f"  pixels      : {pixel_ratio:.0%} de la source")
    print(f"  inclinaison : {result.deskew_angle:+.2f}°")
    print(f"  étapes      : {' → '.join(result.steps_applied)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
