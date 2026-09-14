"""Mise en forme d'une transcription dactylographiée.

**Tranché le 2026-09-14** sur le règlement de copropriété de 44 pages : le modèle
vision recopie la mise en ligne de la machine à écrire — mots coupés en fin de
ligne, lignes cassées au milieu des phrases, virgules collées, numéros de page un
coup sur deux. Fidèle au papier, illisible à l'écran.

Des règles fixes plutôt qu'un LLM : une mise en forme ne doit **toucher à aucune
lettre ni aucun chiffre**, et c'est vérifié (`verify_content_preserved`). Seuls
changent les espaces, les sauts de ligne, les tirets, la ligne du numéro de page
et une clôture de code orpheline.

Le piège est dans les tirets de fin de ligne : la plupart **ne coupent pas un
mot**. Le notaire remplit la fin de ligne de tirets pour qu'on n'y ajoute rien
(`contrat-⏎ne contenait`). Recoller à l'aveugle donnerait `contratne`. Chaque
tiret est donc jugé à l'aide d'un lexique tiré des textes eux-mêmes, et un cas
indécis est **laissé tel quel et signalé**, jamais deviné.
"""

import itertools
import re
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import Enum, auto

from scriptoria.services.text_edits import Edit, Rewrite, apply_edits, remap_span

__all__ = [
    "LayoutError",
    "NormalizedPage",
    "PageText",
    "build_lexicon",
    "normalize_pages",
    "verify_content_preserved",
]

_L = r"[^\W\d_]"  # une lettre, accentuée ou non
_LETTER = re.compile(_L)
_WORD = re.compile(rf"{_L}+")
_COMPOUND = re.compile(rf"{_L}+(?:-{_L}+)+")

# Autour d'un tiret de coupure — en fin de ligne, ou remis en ligne par le modèle
# (`com - prenant`) — les deux mots sont peut-être des fragments : « niers » admis
# au lexique ferait prendre `de-⏎niers` pour du remplissage.
_BREAK_CONTEXT = re.compile(rf"{_L}*(?:-+[ \t]*\n[ \t]*|[ \t]+-[ \t]*|-[ \t]+){_L}*")
_LINE_END_DASH = re.compile(
    rf"(?P<left>{_L}*)(?P<space>[ \t]*)(?P<dashes>-+)[ \t]*\n[ \t]*(?=(?P<right>{_L}+)?)"
)
_PAGE_END_DASH = re.compile(rf"(?P<left>{_L}*)(?P<space>[ \t]*)(?P<dashes>-+)\s*\Z")
_PAGE_START_WORD = re.compile(rf"\A\s*(?P<right>{_L}+)(?P<punct>[.,;:!?)»]*)[ \t]*")
_INLINE_FILLER = re.compile(
    rf"(?P<between>(?<={_L})-{{2,}}(?={_L}))"  # `vingt--trois` → `vingt-trois`
    r"|(?<=\S)[ \t]*-{2,}(?=[ \t])"  # `neuf-- cent` → `neuf cent`
)
_COMMA_BEFORE_LETTER = re.compile(rf",(?={_L})")
_INNER_SPACES = re.compile(r"[ \t]{2,}|\t")

# Formes relevées : `-3-`, `-IO-` (I et O tapés pour 1 et 0), `- II-`, `14-`, `12`.
_PAGE_NUMBER_LINE = re.compile(r"-?[ \t]*(?P<value>[0-9IO]{1,3})[ \t]*-?")
_FENCE_LINE = re.compile(r"```[\w-]*")
_LIST_ITEM = re.compile(
    r"(?:[-*•+][ \t]|\d{1,3}[ \t]?°|[IVXLC]{1,6}[ \t]?[-.°)][ \t]"
    r"|\d{1,3}[.)][ \t]|[a-z]\)[ \t-])"  # `f)- la consommation`, relevé p. 37
)
# `com - prenant` : une coupure que le modèle a déjà remise sur une seule ligne.
_INLINE_SPACED_DASH = re.compile(
    rf"(?P<left>{_L}+)(?P<gap>[ \t]+-[ \t]*|-[ \t]+)(?=(?P<right>{_L}+))"
)
_SENTENCE_END = tuple(".:;!?»")

# Un nombre écrit en toutes lettres garde son tiret : `vingt-⏎sept` → `vingt-sept`.
_NUMBER_HEADS = frozenset({"dix", "vingt", "trente", "quarante", "cinquante", "soixante", "quatre"})
_NUMBER_TAIL = re.compile(
    r"(?:un|une|et|deux|trois|quatre|cinq|six|sept|huit|neuf|dix|onze|douze|treize"
    r"|quatorze|quinze|seize|vingts?)"
    r"|(?:un|deux|trois|quatr|cinqu|six|sept|huit|neuv|dix|onz|douz|treiz|quatorz"
    r"|quinz|seiz|vingt)ièmes?"
)

# Il faut deux pages concordantes pour croire à une numérotation : un nombre seul
# en tête d'une page unique peut être un numéro d'article.
MIN_PAGE_NUMBER_EVIDENCE = 2


class LayoutError(RuntimeError):
    """La mise en forme a changé une lettre ou un chiffre : aucune révision n'est écrite."""


@dataclass(frozen=True)
class PageText:
    """Texte d'une page. Une page non modifiable (relue par un humain) sert de contexte."""

    page_number: int
    text: str
    editable: bool = True


@dataclass(frozen=True)
class NormalizedPage:
    page_number: int
    # Du texte d'origine au texte mis en forme : de quoi reporter les blocs de confiance.
    rewrite: Rewrite
    # Tirets qu'on n'a pas su juger, repérés dans le texte mis en forme.
    uncertain: tuple[tuple[int, int], ...]

    @property
    def text(self) -> str:
        return self.rewrite.text


class _Break(Enum):
    HYPHENATION = auto()  # `exis-⏎tence` → `existence`
    COMPOUND = auto()  # `vingt-⏎sept` → `vingt-sept`
    FILLER = auto()  # `contrat-⏎ne` → `contrat ne`
    UNCERTAIN = auto()  # `ma-⏎risés` : laissé tel quel


class _Line(Enum):
    PROSE = auto()
    LIST = auto()
    TITLE = auto()
    STRUCTURAL = auto()  # tableau, titre Markdown, ligne sans lettre


@dataclass
class _Page:
    """Page en cours de mise en forme : la réécriture cumulée et ses zones indécises."""

    page_number: int
    rewrite: Rewrite
    uncertain: list[tuple[int, int]]

    def apply(self, step: Rewrite, uncertain: Iterable[tuple[int, int]] = ()) -> None:
        """Enchaîne une étape. `uncertain` est repéré dans le texte d'entrée de `step`."""
        spans = [*self.uncertain, *uncertain]
        self.uncertain = [remap_span(step, start, end) for start, end in spans]
        self.rewrite = self.rewrite.then(step)


# --- Lexique ----------------------------------------------------------------


def build_lexicon(texts: Iterable[str]) -> frozenset[str]:
    """Mots vus entiers dans ces textes, en minuscules, mots composés compris.

    Les mots qui bordent un tiret de fin de ligne sont écartés : ce sont peut-être
    des fragments. Un composé apporte aussi ses débuts (`vis-à` pour `vis-à-vis`),
    parce qu'une coupure de ligne n'en montre que le premier morceau.
    """
    words: set[str] = set()
    for text in texts:
        cleaned = _BREAK_CONTEXT.sub(" ", text)
        words.update(word.lower() for word in _WORD.findall(cleaned))
        for compound in _COMPOUND.findall(cleaned):
            parts = compound.lower().split("-")
            words.update("-".join(parts[:end]) for end in range(2, len(parts) + 1))
    return frozenset(words)


def _without_edge_fragments(text: str) -> str:
    """Retire ce qui peut être une moitié de mot coupé entre deux pages."""
    text = re.sub(rf"{_L}+-+\s*\Z", "", text)
    start = re.match(rf"\s*({_L}+)", text)
    if start is not None and start.group(1)[0].islower():
        text = text[start.end() :]
    return text


def _classify(left: str, spaced: bool, right: str | None, lexicon: frozenset[str]) -> _Break:
    """Juge un tiret de fin de ligne d'après les mots qui l'entourent."""
    if spaced or not left or right is None or not right[0].islower():
        return _Break.FILLER
    head, tail = left.lower(), right.lower()
    if head in _NUMBER_HEADS and _NUMBER_TAIL.fullmatch(tail):
        return _Break.COMPOUND
    # `II-⏎rant` : un mot en capitales ne se prolonge pas en minuscules.
    if left.isupper() and len(left) > 1:
        return _Break.UNCERTAIN
    if head + tail in lexicon:
        return _Break.HYPHENATION
    if f"{head}-{tail}" in lexicon:
        return _Break.COMPOUND
    head_known, tail_known = head in lexicon, tail in lexicon
    if head_known and tail_known:
        return _Break.FILLER
    if not head_known and not tail_known:
        # Aucune des deux moitiés n'est un mot : ce ne peut être qu'une césure.
        return _Break.HYPHENATION
    return _Break.UNCERTAIN


# --- Clôture orpheline et numéro de page ------------------------------------


def _line_spans(text: str) -> list[tuple[int, int]]:
    spans = []
    position = 0
    for line in text.split("\n"):
        spans.append((position, position + len(line)))
        position += len(line) + 1
    return spans


def _edge_lines(text: str) -> list[tuple[int, int]]:
    """Première et dernière lignes non vides (une seule si c'est la même)."""
    filled = [(start, end) for start, end in _line_spans(text) if text[start:end].strip()]
    return list(dict.fromkeys([filled[0], filled[-1]])) if filled else []


def _remove_line(text: str, start: int, end: int) -> Edit:
    if end < len(text):
        return Edit(start, end + 1, "")
    return Edit(max(start - 1, 0), end, "")


def _strip_orphan_fence(text: str) -> Rewrite:
    """` ```markdown ` laissé seul par le modèle : un artefact de dialogue, pas la page."""
    if text.count("```") != 1:
        return Rewrite.identity(text)
    for start, end in _edge_lines(text):
        if _FENCE_LINE.fullmatch(text[start:end].strip()):
            return apply_edits(text, [_remove_line(text, start, end)])
    return Rewrite.identity(text)


def _page_number_value(line: str) -> int | None:
    match = _PAGE_NUMBER_LINE.fullmatch(line.strip())
    if match is None:
        return None
    return int(match.group("value").replace("I", "1").replace("O", "0"))


def _page_number_offset(pages: Sequence[tuple[int, str]]) -> int | None:
    """Écart entre le rang de la page et le numéro imprimé, s'il est attesté."""
    offsets: Counter[int] = Counter()
    for page_number, text in pages:
        values = {_page_number_value(text[start:end]) for start, end in _edge_lines(text)}
        offsets.update(page_number - value for value in values if value is not None)
    if not offsets:
        return None
    offset, count = offsets.most_common(1)[0]
    return offset if count >= MIN_PAGE_NUMBER_EVIDENCE else None


def _strip_page_number(text: str, page_number: int, offset: int | None) -> Rewrite:
    if offset is None:
        return Rewrite.identity(text)
    edits = [
        _remove_line(text, start, end)
        for start, end in _edge_lines(text)
        if _page_number_value(text[start:end]) == page_number - offset
    ]
    return apply_edits(text, edits[:1])


# --- Mot coupé entre deux pages ---------------------------------------------


def _has_letter_before(text: str, index: int) -> bool:
    """La ligne qui mène à `index` porte au moins une lettre (`-3-` n'en a pas)."""
    return bool(_LETTER.search(text, text.rfind("\n", 0, index) + 1, index))


def _stitch(pages: list[_Page], lexicon: frozenset[str]) -> None:
    """Recolle sur la page où il commence un mot coupé en fin de page.

    Seules deux pages modifiables et consécutives se touchent : une page relue
    par un humain ne se réécrit pas, même pour y retirer un fragment.
    """
    edits: dict[int, list[Edit]] = {page.page_number: [] for page in pages}
    uncertain: dict[int, list[tuple[int, int]]] = {page.page_number: [] for page in pages}
    cut_until: dict[int, int] = {}

    for current, following in itertools.pairwise(pages):
        if following.page_number != current.page_number + 1:
            continue
        text = current.rewrite.text
        end = _PAGE_END_DASH.search(text)
        if end is None or end.start() < cut_until.get(current.page_number, 0):
            continue
        if not _has_letter_before(text, end.start("dashes")):
            continue

        start = _PAGE_START_WORD.match(following.rewrite.text)
        right = start.group("right") if start is not None else None
        kind = _classify(end.group("left"), bool(end.group("space")), right, lexicon)

        if kind is _Break.UNCERTAIN:
            uncertain[current.page_number].append((end.start("left"), end.end("dashes")))
        elif kind is _Break.FILLER:
            edits[current.page_number].append(Edit(end.start("space"), end.end("dashes"), ""))
        elif start is not None:
            joint = "-" if kind is _Break.COMPOUND else ""
            moved = joint + start.group("right") + start.group("punct")
            edits[current.page_number].append(Edit(end.start("space"), end.end(), moved))
            edits[following.page_number].append(Edit(0, start.end(), ""))
            cut_until[following.page_number] = start.end()

    for page in pages:
        step = apply_edits(page.rewrite.text, edits[page.page_number])
        page.apply(step, uncertain[page.page_number])


# --- Règles d'une page ------------------------------------------------------


def _join_line_ends(text: str, lexicon: frozenset[str]) -> tuple[Rewrite, list[tuple[int, int]]]:
    edits: list[Edit] = []
    uncertain: list[tuple[int, int]] = []
    for match in _LINE_END_DASH.finditer(text):
        if not _has_letter_before(text, match.start("dashes")):
            continue
        right = match.group("right")
        span = (match.start("space"), match.end())
        if _LIST_ITEM.match(_line_around(text, match.end()).lstrip()):
            # `papeterie-⏎f)- la consommation` : la ligne suivante ouvre une énumération.
            edits.append(Edit(*span, "\n"))
            continue
        kind = _classify(match.group("left"), bool(match.group("space")), right, lexicon)
        if kind is _Break.UNCERTAIN:
            uncertain.append((match.start("left"), match.end() + len(right or "")))
        elif kind is _Break.HYPHENATION:
            edits.append(Edit(*span, ""))
        elif kind is _Break.COMPOUND:
            edits.append(Edit(*span, "-"))
        elif right is not None and right[0].islower():
            edits.append(Edit(*span, " "))
        else:
            # Ligne remplie jusqu'au bout avant une majuscule : l'alinéa est fini.
            edits.append(Edit(*span, "\n\n" if right is not None else "\n"))
    return apply_edits(text, edits), uncertain


def _line_around(text: str, index: int) -> str:
    start = text.rfind("\n", 0, index) + 1
    end = text.find("\n", index)
    return text[start : end if end != -1 else len(text)]


def _remove_inline_filler(text: str) -> Rewrite:
    """`mil neuf-- cent` → `mil neuf cent`, hors tableaux et lignes sans lettre."""
    edits = []
    for match in _INLINE_FILLER.finditer(text):
        line = _line_around(text, match.start())
        if line.lstrip().startswith("|") or not _LETTER.search(line):
            continue
        edits.append(Edit(match.start(), match.end(), "-" if match.group("between") else ""))
    return apply_edits(text, edits)


_INLINE_REPLACEMENTS = {_Break.HYPHENATION: "", _Break.COMPOUND: "-", _Break.FILLER: " "}


def _join_inline_dashes(
    text: str, lexicon: frozenset[str]
) -> tuple[Rewrite, list[tuple[int, int]]]:
    """Juge un tiret entouré d'espaces comme un tiret de fin de ligne.

    Relevé p. 20 : le modèle a parfois déjà remis la coupure en ligne, mais en
    gardant le tiret (`com - prenant`, `un appartement - comprenant`). Devant une
    majuscule, ou après un nom en capitales (`Me DURAND - notaire`), c'est un
    tiret d'incise : il reste, et rien n'est à signaler.
    """
    edits: list[Edit] = []
    uncertain: list[tuple[int, int]] = []
    for match in _INLINE_SPACED_DASH.finditer(text):
        left, right = match.group("left"), match.group("right")
        if (
            not right[0].islower()
            or (left.isupper() and len(left) > 1)
            or _line_around(text, match.start()).lstrip().startswith("|")
        ):
            continue
        kind = _classify(left, False, right, lexicon)
        if kind is _Break.UNCERTAIN:
            uncertain.append((match.start("left"), match.end("gap") + len(right)))
        else:
            edits.append(Edit(match.start("gap"), match.end("gap"), _INLINE_REPLACEMENTS[kind]))
    return apply_edits(text, edits), uncertain


def _space_commas(text: str) -> Rewrite:
    """`vingt-quatre,lequel` → `vingt-quatre, lequel`. `28,90` n'est pas touché."""
    edits = [
        Edit(match.start(), match.end(), ", ") for match in _COMMA_BEFORE_LETTER.finditer(text)
    ]
    return apply_edits(text, edits)


def _line_kind(line: str) -> _Line:
    if line.startswith(("|", "#", "```", ">")) or not _LETTER.search(line):
        return _Line.STRUCTURAL
    if _LIST_ITEM.match(line):
        return _Line.LIST
    if not any(char.islower() for char in line):
        return _Line.TITLE
    return _Line.PROSE


def _separator(before: str, after: str, blank_between: bool) -> str:
    """Ce qui sépare deux lignes non vides : une espace, un saut de ligne, un alinéa."""
    if blank_between:
        return "\n\n"
    if before.endswith("-"):
        # Tiret indécis laissé en place : on ne recolle pas ce qu'on n'a pas su juger.
        return "\n"
    before_kind, after_kind = _line_kind(before), _line_kind(after)
    if _Line.STRUCTURAL in (before_kind, after_kind) or after_kind is _Line.LIST:
        return "\n"
    if _Line.TITLE in (before_kind, after_kind):
        return "\n\n"
    if before.endswith(_SENTENCE_END) and not after[0].islower():
        return "\n\n"
    if (before[-1].isdigit() or before.endswith("°")) and not after[0].islower():
        # Relevé p. 30 : une liste de lots, un par ligne, qui finit sur sa quote-part.
        return "\n"
    return " "


def _reflow(text: str) -> Rewrite:
    """Recolle les lignes d'une même phrase, sépare les alinéas d'une ligne vide."""
    filled = []
    for start, end in _line_spans(text):
        line = text[start:end]
        content_start = start + len(line) - len(line.lstrip(" \t"))
        content_end = start + len(line.rstrip(" \t"))
        if content_start < content_end:
            filled.append((content_start, content_end))
    if not filled:
        return apply_edits(text, [Edit(0, len(text), "")])

    edits = [Edit(0, filled[0][0], ""), Edit(filled[-1][1], len(text), "")]
    for (before_start, before_end), (after_start, after_end) in itertools.pairwise(filled):
        separator = _separator(
            text[before_start:before_end],
            text[after_start:after_end],
            blank_between=text.count("\n", before_end, after_start) >= 2,
        )
        edits.append(Edit(before_end, after_start, separator))
    for start, end in filled:
        edits.extend(
            Edit(match.start(), match.end(), " ")
            for match in _INNER_SPACES.finditer(text, start, end)
        )
    return apply_edits(
        text, [edit for edit in edits if text[edit.start : edit.end] != edit.replacement]
    )


# --- Garde-fou et point d'entrée --------------------------------------------


def _signature(text: str) -> str:
    return "".join(char for char in text if char.isalnum())


def verify_content_preserved(
    before: Sequence[tuple[int, str]], after: Sequence[tuple[int, str]]
) -> None:
    """Vérifie qu'aucune lettre ni aucun chiffre n'a changé, pages mises bout à bout.

    Bout à bout, parce qu'un fragment recollé passe légitimement d'une page à
    l'autre. Une mise en forme qui changerait `28,90` en `28,60` serait une
    corruption silencieuse — le défaut que ce projet redoute le plus.

    Raises:
        LayoutError: en nommant la première page où le contenu diverge.
    """
    expected = "".join(_signature(text) for _, text in before)
    actual = "".join(_signature(text) for _, text in after)
    if expected == actual:
        return

    divergence = next(
        (
            index
            for index, pair in enumerate(zip(expected, actual, strict=False))
            if pair[0] != pair[1]
        ),
        min(len(expected), len(actual)),
    )
    page_number = after[-1][0] if after else before[-1][0]
    consumed = 0
    for number, text in after:
        consumed += len(_signature(text))
        if divergence < consumed:
            page_number = number
            break
    raise LayoutError(
        f"la mise en forme a modifié le contenu de la page {page_number} : révision refusée"
    )


def normalize_pages(
    pages: Sequence[PageText], known_texts: Iterable[str] = ()
) -> list[NormalizedPage]:
    """Met en forme les pages modifiables d'un document, dans l'ordre des pages.

    Toutes les pages, modifiables ou non, servent au lexique et à reconnaître la
    numérotation ; seules les modifiables sont rendues.

    Args:
        pages: les pages du document.
        known_texts: d'autres textes dont les mots enrichissent le lexique.

    Raises:
        LayoutError: une lettre ou un chiffre aurait changé.
    """
    ordered = sorted(pages, key=lambda page: page.page_number)
    unwrapped = [_strip_orphan_fence(page.text) for page in ordered]
    offset = _page_number_offset(
        [(page.page_number, rewrite.text) for page, rewrite in zip(ordered, unwrapped, strict=True)]
    )
    prepared = [
        rewrite.then(_strip_page_number(rewrite.text, page.page_number, offset))
        for page, rewrite in zip(ordered, unwrapped, strict=True)
    ]
    lexicon = build_lexicon(
        [_without_edge_fragments(rewrite.text) for rewrite in prepared] + list(known_texts)
    )

    working = [
        _Page(page.page_number, rewrite, [])
        for page, rewrite in zip(ordered, prepared, strict=True)
        if page.editable
    ]
    before = [(page.page_number, page.rewrite.text) for page in working]

    _stitch(working, lexicon)
    for page in working:
        joined, uncertain = _join_line_ends(page.rewrite.text, lexicon)
        page.apply(joined, uncertain)
        page.apply(_remove_inline_filler(page.rewrite.text))
        inline, uncertain = _join_inline_dashes(page.rewrite.text, lexicon)
        page.apply(inline, uncertain)
        page.apply(_space_commas(page.rewrite.text))
        page.apply(_reflow(page.rewrite.text))

    verify_content_preserved(before, [(page.page_number, page.rewrite.text) for page in working])
    return [
        NormalizedPage(page.page_number, page.rewrite, tuple(page.uncertain)) for page in working
    ]
