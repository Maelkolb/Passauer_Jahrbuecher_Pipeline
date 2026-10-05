"""Personal-name parsing for TOC entries, article bylines and contributor lists.

The TOC of a *Passauer Jahrbuch* volume names authors in several shapes::

    Hartmut Wolff/Walter Wandling, Lateinische Inschriften …       (comma form)
    Sebastian Gassner, Nina Kunze, Thomas Maurer, Malte Rehbein: …  (colon form)
    Franziska, Karl und Georg R. Rettenbacher, Goldstickerei …     (shared surname)
    Katharina Weigand, Jörg Zedler, Florian Schuller (Hg.), …       (editors)
    Egon Boshof, Die Regesten der Bischöfe von Passau. (Andreas Fohrer)
                                                     ^ review: the reviewer
                                                       is the author of the
                                                       article, Boshof wrote
                                                       the reviewed book

The comma form is ambiguous — "Ludwig Schießl, Siegfried Bräuer, Dialektpflege
in Bayern" has two authors, "Alltag, der nicht alltäglich war" has none — so a
chunk only counts as a name when it *looks* like one: capitalised name words,
optional initials and nobiliary particles, a surname at the end, and a first
token that is a known given name (``data/first_names.txt``, extended at run
time with the given names from the volume's contributor list) or an initial.

Everything here is pure string processing so it is cheap to unit-test.
"""

from __future__ import annotations

import html as html_module
import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple


# ---------------------------------------------------------------------------
# Lexicon
# ---------------------------------------------------------------------------

_LEXICON_PATH = Path(__file__).resolve().parent.parent / "data" / "first_names.txt"


@lru_cache(maxsize=1)
def base_given_names() -> frozenset:
    """Lower-cased given names from ``data/first_names.txt``."""
    names: Set[str] = set()
    try:
        for line in _LEXICON_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                names.add(line.lower())
    except FileNotFoundError:  # pragma: no cover - packaging problem
        pass
    return frozenset(names)


# Lower-case tokens that may sit inside a personal name.
PARTICLES = {
    "von", "van", "de", "der", "den", "zu", "zur", "vom", "di", "da", "del",
    "della", "la", "le", "ten", "ter", "y", "v.", "d'",
}

# Capitalised words that start a title rather than a name.
_TITLE_STARTERS = {
    "der", "die", "das", "des", "dem", "ein", "eine", "einer", "eines", "zur",
    "zum", "vom", "im", "in", "aus", "über", "ueber", "und", "mit", "für",
    "fuer", "bei", "nach", "auf", "an", "am", "als", "wie", "was", "wer",
    "warum", "vor", "unter", "zwischen", "neue", "neues", "neuer", "alte",
    "st.", "hl.", "sankt", "kloster", "stadt", "markt", "burg", "schloss",
    "bayern", "passau", "geschichte", "chronik", "bericht", "berichte",
    "verzeichnis", "register", "orts", "forum", "anzeigen", "vorbemerkung",
    "abkürzungen", "nachruf", "in memoriam", "the", "a",
}

# Academic titles / honorifics dropped from names.
_HONORIFIC_RE = re.compile(
    r"\b(?:Prof\.|Dr\.(?:\s*phil\.|\s*theol\.|\s*jur\.|\s*med\.)?|Dipl\.-\w+\.|"
    r"Mag\.|em\.|apl\.|Univ\.-Prof\.|PD|M\.\s?A\.)\s*"
)

# Role markers that follow a name list: editors, compilers, deceased.
_ROLE_RE = re.compile(
    r"\(\s*(?P<role>Hgg?\.|Hrsgg?\.|Hrsg\.|Bearb\.|bearb\.|eds?\.|Red\.|†)\s*\)"
)
_EDITOR_ROLES = {"Hg.", "Hgg.", "Hrsg.", "Hrsgg.", "Bearb.", "bearb.", "ed.", "eds.", "Red."}

# Separators between persons inside one name list.
_JOIN_SPLIT = re.compile(
    r"\s*/\s*|\s*;\s*|\s+und\s+|\s+u\.\s+|\s*&\s*|\s+and\s+"
    r"|\s+unter\s+Mitwirkung\s+von\s+|\s+in\s+Zusammenarbeit\s+mit\s+"
)


def _strip_diacritics(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    return "".join(c for c in s if not unicodedata.combining(c))


def name_key(name: str) -> str:
    """Comparison key: lower-case, no diacritics, no punctuation."""
    s = _strip_diacritics(name or "").lower().replace("ß", "ss")
    return re.sub(r"[^a-z]+", " ", s).strip()


def surname_key(name: str) -> str:
    """Key of the last name word, hyphenated parts kept together
    ("Röhrer-Ertl" → "rohrerertl", not "ertl")."""
    toks = (name or "").split()
    return name_key(toks[-1]).replace(" ", "") if toks else ""


# ---------------------------------------------------------------------------
# Token classification
# ---------------------------------------------------------------------------

def _is_initial(tok: str) -> bool:
    """"W." / "H.-J." / "T.E." style initials."""
    if not tok.endswith("."):
        return False
    core = tok.replace("-", "").replace(".", "")
    return 1 <= len(core) <= 3 and all(c.isalpha() and c.isupper() for c in core)


def _is_name_word(tok: str) -> bool:
    """A capitalised word made of letters, inner hyphens or apostrophes
    ("Christl-Sorcan", "O'Neill", "Spurný"). All-caps words of more than
    two letters are rejected — bylines are title-cased before parsing."""
    if not tok or not tok[0].isalpha() or not tok[0].isupper():
        return False
    if tok.endswith("-") or tok.endswith("'"):
        return False
    body = tok.replace("-", "").replace("'", "").replace("’", "")
    if not body.isalpha():
        return False
    if len(body) > 2 and body.isupper():
        return False
    # every hyphen-separated part must itself be capitalised ("Hans-Jürgen",
    # but not "Orts-und")
    for part in tok.split("-"):
        if part and not part[0].isupper():
            return False
    return True


def _is_given(tok: str, given: Set[str]) -> bool:
    if _is_initial(tok):
        return True
    parts = [p for p in tok.split("-") if p]
    return bool(parts) and all(p.lower() in given for p in parts)


# Endings of capitalised German adjectives/nouns that start book titles
# ("Lebendiges Büchererbe", "Römische Gutshöfe") but never given names.
_NON_GIVEN_SUFFIXES = (
    "iges", "iger", "ige", "isches", "ischer", "ische", "isch", "liches",
    "licher", "liche", "ungen", "ung", "heit", "keit", "schaft", "tum",
    "nis", "ismus", "ität", "ien", "isten", "ianer", "burger", "auer",
    "ener", "aner", "ler", "ischen", "lichen", "werk", "buch", "bücher",
)


def _plausible_given(tok: str) -> bool:
    """Relaxed given-name test for names missing from the lexicon."""
    if _is_initial(tok):
        return True
    low = tok.lower()
    if low in _TITLE_STARTERS or len(low) < 3:
        return False
    return not any(low.endswith(suf) for suf in _NON_GIVEN_SUFFIXES)


def _tokens(s: str) -> List[str]:
    return [t for t in re.split(r"\s+", s.strip()) if t]


def clean_name(s: str) -> str:
    """Drop honorifics, role markers, "†" and stray punctuation around a
    name; nobiliary particles inside a name are lower-cased ("Marc Von
    Knorring" → "Marc von Knorring")."""
    s = _ROLE_RE.sub(" ", s or "")
    s = _HONORIFIC_RE.sub("", s)
    s = re.sub(r"^(?:Frau|Herr|Fr\.|Hr\.)\s+", "", s.strip())
    s = s.replace("†", " ")
    s = re.sub(r"\s+", " ", s).strip(" ,.;:")
    toks = s.split(" ")
    for k in range(1, len(toks) - 1):
        if toks[k].lower() in ("von", "zu", "van", "de", "der", "den", "und"):
            toks[k] = toks[k].lower()
    return " ".join(toks)


def looks_like_person(
    s: str,
    given: Set[str],
    *,
    strict: bool = True,
    known: Optional[Set[str]] = None,
    allow_single: bool = False,
    relaxed: bool = False,
) -> bool:
    """Does ``s`` (already cleaned) look like one personal name?

    ``strict`` requires the first token to be a known given name or an
    initial; ``relaxed`` loosens that to "anything that does not look like
    a capitalised adjective or noun". ``known`` is a set of :func:`name_key`
    values that are accepted regardless (names confirmed by a byline or the
    contributor list). ``allow_single`` accepts a bare surname
    ("Heydenreuter") — used for the reviewer parenthetical of older volumes.
    """
    if not s:
        return False
    if known and name_key(s) in known:
        return True
    toks = _tokens(s)
    if not toks or len(toks) > 6:
        return False
    if toks[0].lower() in _TITLE_STARTERS:
        return False
    core = [t for t in toks if t.lower() not in PARTICLES]
    if not core:
        return False
    for k, t in enumerate(toks):
        low = t.lower()
        if low in PARTICLES:
            # "der"/"den" only as part of "von der", "van den", "ter" …
            if low in ("der", "den") and (k == 0 or toks[k - 1].lower() not in ("von", "van", "ter", "de")):
                return False
            continue
        if not (_is_name_word(t) or _is_initial(t)):
            return False
    if not _is_name_word(core[-1]):
        return False
    if len(core) == 1:
        return allow_single and len(core[-1]) >= 3
    if strict and not _is_given(core[0], given):
        return relaxed and _plausible_given(core[0])
    return True


# ---------------------------------------------------------------------------
# Name lists ("A / B", "A, B und C", "Franziska, Karl und Georg R. X")
# ---------------------------------------------------------------------------

@dataclass
class NameList:
    names: List[str] = field(default_factory=list)
    roles: List[str] = field(default_factory=list)   # "author" / "editor" per name


def parse_name_list(
    s: str,
    given: Set[str],
    *,
    strict: bool = True,
    known: Optional[Set[str]] = None,
    allow_single: bool = False,
    comma_is_separator: bool = True,
    relaxed: bool = False,
) -> Optional[NameList]:
    """Parse a list of persons, or return ``None`` if any piece is not a name.

    Handles ``/``, ``;``, ``und``, ``u.``, ``&``, "unter Mitwirkung von" and
    (optionally) commas as separators, ``(Hg.)``-style role markers (which
    make every name before them in the list an editor), ``†``, and shared
    surnames ("Franziska, Karl und Georg R. Rettenbacher").

    In strict mode a list of two or more persons is accepted when at least
    one of them has a known given name and the others pass the relaxed
    test — the list structure itself is evidence ("Peter Morsbach / Wilkin
    Spitta").
    """
    if not s or not s.strip():
        return None
    text = re.sub(r"\s+", " ", s).strip(" ,.;:")
    role_editor = False
    for m in _ROLE_RE.finditer(text):
        if m.group("role") in _EDITOR_ROLES:
            role_editor = True
    text = _ROLE_RE.sub(" ", text)
    text = _HONORIFIC_RE.sub("", text)
    text = text.replace("†", " ")
    text = re.sub(r"\s+", " ", text).strip(" ,.;:")
    if not text:
        return None

    pieces = _JOIN_SPLIT.split(text)
    if comma_is_separator:
        pieces = [q for p in pieces for q in p.split(",")]
    pieces = [clean_name(p) for p in pieces]
    pieces = [p for p in pieces if p]
    if not pieces:
        return None

    names: List[str] = []
    pending_given: List[str] = []   # "Franziska", "Karl" waiting for a surname
    n_strict = 0
    for i, p in enumerate(pieces):
        toks = _tokens(p)
        is_last = i == len(pieces) - 1
        if (len(toks) == 1 and not is_last and _is_name_word(toks[0])
                and toks[0].lower() in given):
            pending_given.append(toks[0])
            continue
        if looks_like_person(p, given, strict=strict, known=known,
                             allow_single=allow_single):
            n_strict += 1
        elif not looks_like_person(p, given, strict=strict, known=known,
                                   allow_single=allow_single,
                                   relaxed=relaxed or len(pieces) > 1):
            return None
        if pending_given:
            core = [t for t in toks if t.lower() not in PARTICLES]
            surname_start = toks.index(core[-1])
            # include a nobiliary particle directly before the surname
            while surname_start > 0 and toks[surname_start - 1].lower() in PARTICLES:
                surname_start -= 1
            surname = " ".join(toks[surname_start:])
            names.extend(f"{g} {surname}" for g in pending_given)
            pending_given = []
        names.append(p)
    if pending_given:
        return None
    if strict and not relaxed and n_strict == 0 and not pending_given:
        # every piece only passed the relaxed test
        if len(names) < 2 or not any(_is_given(_tokens(n)[0], given) for n in names):
            return None
    role = "editor" if role_editor else "author"
    return NameList(names=names, roles=[role] * len(names))


# ---------------------------------------------------------------------------
# TOC entries
# ---------------------------------------------------------------------------

@dataclass
class EntryParse:
    title: str
    authors: List[str] = field(default_factory=list)      # who wrote this article
    editors: List[str] = field(default_factory=list)      # (Hg.) of the cited work
    is_review: bool = False
    reviewers: List[str] = field(default_factory=list)    # raw reviewer names
    reviewed_authors: List[str] = field(default_factory=list)
    reviewed_editors: List[str] = field(default_factory=list)
    reviewed_title: str = ""
    form: str = ""                                        # colon / comma / review / none


# "(Andreas Fohrer)" / "(Heydenreuter)" / "(M. Kobler)" at the very end.
_TRAILING_PAREN = re.compile(r"\(\s*(?P<inner>[^()]{2,90}?)\s*\)\s*\.?\s*$")


def _reviewer_list(inner: str, given: Set[str], known: Optional[Set[str]]) -> Optional[List[str]]:
    inner = inner.strip()
    if not inner or inner[0] in "=0123456789" or inner.startswith("Hg") or inner.startswith("Hrsg"):
        return None
    if re.search(r"\d", inner):
        return None
    nl = parse_name_list(inner, given, strict=False, known=known,
                         allow_single=True, comma_is_separator=False)
    return nl.names if nl else None


_TITLE_START_OK = "„»\"'‚«“(["


def _title_ok(title: str) -> bool:
    title = title.strip()
    return bool(title) and (title[0].isupper() or title[0].isdigit()
                            or title[0] in _TITLE_START_OK)


def _split_comma_form(
    text: str,
    given: Set[str],
    known: Optional[Set[str]],
    *,
    relaxed: bool = False,
) -> Optional[Tuple[NameList, str]]:
    """Take leading comma chunks while they are names; the rest is the title.

    Also accepts a role marker directly followed by the title without a
    comma ("Wolfgang Janka (Bearb.) Regesten der Urkunden …").
    """
    m = re.match(r"^(?P<names>[^,():]{3,120}?\((?:Hgg?\.|Hrsgg?\.|Bearb\.|bearb\.)\))\s+(?P<title>[^,].*)$", text)
    if m:
        nl = parse_name_list(m.group("names"), given, strict=True, known=known,
                             relaxed=relaxed)
        if nl is not None and _title_ok(m.group("title")):
            return nl, m.group("title").strip()

    chunks = [c for c in re.split(r",\s+", text)]
    if len(chunks) < 2:
        return None
    names: List[str] = []
    roles: List[str] = []
    i = 0
    while i < len(chunks) - 1:
        chunk = chunks[i]
        nl = parse_name_list(chunk, given, strict=True, known=known,
                             comma_is_separator=False, relaxed=relaxed and i == 0)
        if nl is None:
            # "Franziska, Karl und Georg R. Rettenbacher": a bare given name
            # is only a name chunk if the following chunks complete it.
            absorbed = False
            for j in range(i + 1, min(i + 4, len(chunks) - 1)):
                joined = ", ".join(chunks[i:j + 1])
                nl2 = parse_name_list(joined, given, strict=True, known=known)
                if nl2 is not None and len(nl2.names) == j - i + 1 + \
                        sum(len(_JOIN_SPLIT.split(c)) - 1 for c in chunks[i:j + 1]):
                    names.extend(nl2.names)
                    roles.extend(nl2.roles)
                    i = j + 1
                    absorbed = True
                    break
            if absorbed:
                if "editor" in roles:
                    roles = ["editor"] * len(roles)
                    break
                continue
            break
        names.extend(nl.names)
        roles.extend(nl.roles)
        i += 1
        if "editor" in nl.roles:
            # "(Hg.)" closes the list; everything before it are editors too
            roles = ["editor"] * len(roles)
            break
    if not names:
        return None
    title = ", ".join(chunks[i:]).strip()
    if not _title_ok(title):
        return None
    return NameList(names, roles), title


def _split_colon_form(
    text: str,
    given: Set[str],
    known: Optional[Set[str]],
) -> Optional[Tuple[NameList, str]]:
    """"A, B und C: Title" — only the first colon can close the name list."""
    cm = re.search(r":\s+", text)
    if not cm:
        return None
    left, right = text[:cm.start()], text[cm.end():].strip()
    if not right or len(_tokens(left)) > 30:
        return None
    nl = parse_name_list(left, given, strict=True, known=known, relaxed=True)
    if nl is None:
        return None
    return nl, right


# "hrsg. von Erwin Gatz", "bearbeitet von Susanne Kropač", "redigiert von …"
_EMBEDDED_EDITORS = re.compile(
    r"\b(?:hrsg\.|herausgegeben|bearb\.|bearbeitet|redigiert|zusammengestellt|eingeleitet)"
    r"\s+(?:von|v\.)\s+(?P<names>(?:[^,.;()]|\b[A-ZÄÖÜ]\.)+?)(?=\s*(?:,|;|\(|\)|\.\s|\.$|unter\b|u\.\s*a\.|$))"
)
# "Nachruf auf Willibald Ernst von Walter Hartinger"
_TRAILING_VON = re.compile(r"\s+von\s+(?P<name>[^,.;()]+?)\s*$")


def _roles_split(nl: NameList) -> Tuple[List[str], List[str]]:
    authors = [n for n, r in zip(nl.names, nl.roles) if r == "author"]
    editors = [n for n, r in zip(nl.names, nl.roles) if r == "editor"]
    return authors, editors


def _parse_citation(body: str, given: Set[str], known: Optional[Set[str]]):
    """Book citation of a review → (authors, editors, title)."""
    for fn in (lambda: _split_colon_form(body, given, known),
               lambda: _split_comma_form(body, given, known, relaxed=True)):
        res = fn()
        if res:
            nl, title = res
            a, e = _roles_split(nl)
            return a, e, title
    editors: List[str] = []
    for m in _EMBEDDED_EDITORS.finditer(body):
        nl = parse_name_list(m.group("names"), given, strict=False)
        if nl:
            editors.extend(nl.names)
    return [], editors, body


def split_entry(
    text: str,
    given: Optional[Set[str]] = None,
    *,
    known: Optional[Set[str]] = None,
    review_context: bool = False,
    allow_review: bool = True,
) -> EntryParse:
    """Split one TOC entry into authors / title (and reviewer for reviews).

    ``review_context`` is True when the entry sits in a book-review section.
    There a trailing name parenthetical is read as the reviewer, and the
    newer "Reviewer: Book author, Book title" shape is recognised. Outside
    that context a trailing parenthetical still counts when the rest has
    the shape of a book citation (a leading name list and a comma).
    """
    given = set(given or base_given_names())
    t = re.sub(r"\s+", " ", text or "").strip()
    if not t:
        return EntryParse(title="")

    # 1) Reviewer parenthetical at the end: "Book citation (Reviewer)"
    m = _TRAILING_PAREN.search(t) if allow_review else None
    if m:
        reviewers = _reviewer_list(m.group("inner"), given, known)
        if reviewers:
            body = t[:m.start()].strip().rstrip(".").strip()
            authors, editors, book_title = _parse_citation(body, given, known)
            if review_context or authors or editors:
                return EntryParse(
                    title=body, authors=list(reviewers), is_review=True,
                    reviewers=list(reviewers), reviewed_authors=authors,
                    reviewed_editors=editors, reviewed_title=book_title,
                    form="review",
                )
            if (not _split_colon_form(body, given, known)
                    and not _split_comma_form(body, given, known)):
                # "Alfred Fuchs zum Gedenken (P. Praxl)": the author in brackets
                return EntryParse(title=body, authors=list(reviewers), form="paren")

    # 2) Colon form: "A, B und C: Title"
    colon = _split_colon_form(t, given, known)
    if colon:
        nl, right = colon
        authors, editors = _roles_split(nl)
        if review_context:
            # newer volumes: "Reviewer: Book author, Book title"
            b_authors, b_editors, b_title = _parse_citation(right, given, known)
            if b_authors or b_editors or _ROLE_RE.search(right) or "," in right:
                return EntryParse(
                    title=right, authors=nl.names, is_review=True,
                    reviewers=nl.names, reviewed_authors=b_authors,
                    reviewed_editors=b_editors, reviewed_title=b_title,
                    form="review",
                )
        return EntryParse(title=right, authors=authors or editors,
                          editors=editors, form="colon")

    # 3) Comma form: "A/B, Title", "A, B (Hg.), Title"
    cf = _split_comma_form(t, given, known)
    if cf:
        nl, title = cf
        authors, editors = _roles_split(nl)
        return EntryParse(title=title, authors=authors or editors,
                          editors=editors, form="comma")

    # 4) Author named at the end: "Nachruf auf Willibald Ernst von Walter Hartinger"
    tv = _TRAILING_VON.search(t)
    if tv:
        nl = parse_name_list(tv.group("name"), given, strict=True, known=known,
                             comma_is_separator=False)
        if nl is not None:
            return EntryParse(title=t, authors=nl.names, form="trailing")

    return EntryParse(title=t, form="none")


# ---------------------------------------------------------------------------
# Bylines ("HEINZ KELLERMANN", "SEBASTIAN GASSNER, NINA KUNZE …")
# ---------------------------------------------------------------------------

def _titlecase_word(w: str) -> str:
    if not w:
        return w
    low = w.lower()
    if low in PARTICLES:
        return low
    return "-".join(p[:1].upper() + p[1:].lower() if p else p for p in w.split("-"))


def titlecase_name(s: str) -> str:
    """"HANS-WERNER EROMS" → "Hans-Werner Eroms", "MARC VON KNORRING" →
    "Marc von Knorring". Mixed-case input is returned unchanged."""
    letters = [c for c in s if c.isalpha()]
    if letters and not all(c.isupper() for c in letters):
        return s
    return " ".join(_titlecase_word(w) for w in s.split())


def parse_byline(text: str, given: Set[str]) -> Optional[List[str]]:
    """Return the names in a byline block, or ``None`` if it isn't one."""
    t = re.sub(r"\s+", " ", (text or "")).strip()
    if not t or len(t) > 160:
        return None
    t = re.sub(r"^(?:von|by)\s+", "", t, flags=re.I)
    t = re.sub(r"\s+UND\s+", " und ", t)
    t = titlecase_name(t)
    nl = parse_name_list(t, given, strict=False)
    if nl is None:
        return None
    return nl.names


# ---------------------------------------------------------------------------
# Contributor list ("MITARBEITER": "Becker, Winfried, Prof. em. Dr. phil., …")
# ---------------------------------------------------------------------------

_CONTRIB_RE = re.compile(
    r"^\s*(?P<surname>[^\W\d_][\w'’\-]*(?:\s+[^\W\d_][\w'’\-]*)?)\s*,\s*"
    r"(?P<given>(?:[^\W\d_][\w'’\-]*\.?|[A-ZÄÖÜ]\.)(?:\s+(?:[^\W\d_][\w'’\-]*\.?|[A-ZÄÖÜ]\.))*?)"
    r"(?:\s+(?P<particle>von|van|de|zu|von und zu))?\s*(?:,|$)"
)


@dataclass
class Contributor:
    name: str          # "Winfried Becker"
    surname: str
    given: str


def parse_contributor_entries(paragraphs: Iterable[str], given_lex: Set[str]) -> List[Contributor]:
    """Parse "Surname, Given(s)[ particle], …" paragraphs."""
    out: List[Contributor] = []
    seen: Set[str] = set()
    for para in paragraphs:
        first = (para or "").strip().split("\n", 1)[0]
        m = _CONTRIB_RE.match(first)
        if not m:
            continue
        surname = m.group("surname").strip()
        given = m.group("given").strip()
        particle = m.group("particle")
        if not (_is_name_word(surname.split()[-1]) and
                all(_is_name_word(g) or _is_initial(g) for g in given.split())):
            continue
        first_given = given.split()[0]
        if not (_is_given(first_given, given_lex) or len(given.split()) <= 3):
            continue
        name = clean_name(f"{given} {particle + ' ' if particle else ''}{surname}")
        key = name_key(name)
        if key in seen:
            continue
        seen.add(key)
        out.append(Contributor(name=name, surname=surname, given=given))
    return out


def paragraphs_from_block(block: dict) -> List[str]:
    """Split a list/text block into its paragraphs, preferring the HTML."""
    h = block.get("html") or ""
    if h:
        paras = re.split(r"</p>|</li>", h)
        out = []
        for p in paras:
            p = re.sub(r"<br\s*/?>", "\n", p, flags=re.I)
            p = re.sub(r"<[^>]+>", "", p)
            p = html_module.unescape(p).strip()
            if p:
                out.append(p)
        if out:
            return out
    return [ln for ln in (block.get("text") or "").split("\n") if ln.strip()]


class NameResolver:
    """Resolve partial names (surname-only reviewers, abbreviated given
    names) against a volume's contributor list and other known names."""

    def __init__(self, full_names: Iterable[str] = ()):
        self._by_surname: Dict[str, List[str]] = {}
        for n in full_names:
            self.add(n)

    def add(self, full: str) -> None:
        sk = surname_key(full)
        if not sk:
            return
        lst = self._by_surname.setdefault(sk, [])
        if full not in lst:
            lst.append(full)

    def resolve(self, name: str) -> str:
        """Complete an *incomplete* name from the known full names.

        Only a bare surname ("Heydenreuter", "von Knorring"), initials
        ("R. Heydenreuter") or an abbreviated particle ("Mark v. Knorring")
        are completed, and only when exactly one known person fits. A name
        that is already complete is returned unchanged — the contributor
        list is OCR too, and must not overwrite a correct spelling.
        """
        sk = surname_key(name)
        cands = self._by_surname.get(sk, [])
        if not cands:
            return name
        toks = _tokens(name)
        given = [t for t in toks[:-1] if t.lower() not in PARTICLES]
        abbreviated_particle = any(t.lower() == "v." for t in toks[:-1])
        if not given:
            return cands[0] if len(cands) == 1 else name
        if not (all(_is_initial(t) for t in given) or abbreviated_particle):
            return name
        g = given[0].rstrip(".").lower()
        n = 1 if _is_initial(given[0]) else min(3, len(g))
        hits = [c for c in cands if _tokens(c)[0].lower()[:n] == g[:n]]
        return hits[0] if len(hits) == 1 else name

    def keys(self) -> Set[str]:
        return {name_key(n) for lst in self._by_surname.values() for n in lst}

    def given_names(self) -> Set[str]:
        out: Set[str] = set()
        for lst in self._by_surname.values():
            for n in lst:
                for t in _tokens(n)[:-1]:
                    if _is_name_word(t):
                        out.update(p.lower() for p in t.split("-") if p)
        return out
