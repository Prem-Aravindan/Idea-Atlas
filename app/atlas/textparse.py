"""Rule-based note parser.

Turns free text into typed segments (question, idea, method, ...), concept
mentions, definitions, aliases, references and typed relations. Pure
standard library, no models: every decision is a transparent heuristic so the
user can see (and correct) why something was classified the way it was.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# ---------------------------------------------------------------- vocabulary

STOP = set("""
a an the and or but if then else of to in on at by for with from into onto over
under about as is are was were be been being it its this that these those there
here we i you he she they them our ours your my me us do does did done have has
had having not no nor so such than too very can could should would will shall may
might must just also only both each few more most other some any all own same what
which who whom whose why how when where while because since until although though
whether per via vs etc eg ie e.g i.e using use used upon between within without
across after before during through against among up down out off again further
once let lets let's maybe perhaps probably really quite rather still yet even like
get got thing things stuff lot lots way ways something anything everything one ones
another new good better best bad worse much many well am im i'm it's that's what's
""".split())

# Common verbs that should never start/end a noun chunk.
VERBS = set("""
use uses make makes made get gets try tries tried train trains run runs ran show
shows showed shown need needs want wants think thinks thought know knows see sees
saw find finds found build builds built add adds take takes took give gives gave
help helps improve improves reduce reduces increase increases
check checks look looks read reads write writes wrote compare compares test tests
evaluate evaluates implement implements apply applies compute computes learn learns
learned seem seems seemed become becomes became keep keeps put puts set sets go goes
went come comes came say says said call calls called mean means meant feel feels
works worked tried gave give looked wrote
consider considers start starts started stop stops allow allows provide provides
include includes require requires required depend depends rely relies solve solves
solved fix fixes fixed explain explains support supports contradict contradicts
cause causes lead leads drive drives handle handles address addresses correlate
correlates correlated remain remains remained stay stays matter matters happen happens
hold holds held fail fails failed perform performs performed scale scales differ differs
produce produces affect affects predict predicts generate generates
""".split())

ACRO_STOP = set("""I A OK TODO FIXME NOTE FYI ASAP ETA TBD TBC IMO IMHO BTW AKA EG IE ETC NB
PS AM PM RE FW CC""".split())

CAP_STOP = set("""I The A An This That These Those It We You They He She There Here
What Which Who Why How When Where If But And Or So Also Maybe Perhaps Then Now Next
Monday Tuesday Wednesday Thursday Friday Saturday Sunday January February March April
June July August September October November December Yes No Not Note Idea Question
Solution Method Risk Todo Ref Source Evidence Decision Assumption Answer Our My Their
Its Some Any All Each Every Let Lets Should Could Would Can Will Is Are Was Were Do
Does Did Have Has Had Because Since Although However Therefore Thus Still Yet Just""".split())

# time words, vague adjectives and chatty words that make poor concept names
CHUNK_STOP = set("""last next week weeks today yesterday tomorrow day days month months year years time times
morning afternoon evening tonight monday tuesday wednesday thursday friday saturday sunday real main whole
big small huge tiny nice cool kind sort bit lot little same different certain sure clear able likely
something someone somewhere anyone everyone meeting instead anyway maybe probably actually basically
really already though surrounding text ensure""".split())

DETERMINERS = set("""the a an this that these those our their its my your his her some any
all more better good each every such""".split())

NODE_KINDS = ["concept", "method", "idea", "solution", "question", "evidence", "decision",
              "assumption", "risk", "explanation", "reference", "task", "claim"]

KIND_PREFIX = {"concept": "C", "method": "M", "idea": "I", "solution": "S", "question": "Q",
               "evidence": "E", "decision": "D", "assumption": "A", "risk": "R",
               "explanation": "X", "reference": "F", "task": "T", "claim": "K"}

CODE_RE = re.compile(r"\b([CMISQEDARXFTK])-(\d{3,})\b")

# Words in a heading or "Prefix:" that set the kind of what follows.
KIND_WORDS = {
    "concept": "concept concepts term terms glossary keyword keywords definition definitions def key-terms vocabulary",
    "method": "method methods approach approaches technique techniques methodology procedure protocol pipeline how-to algorithm",
    "idea": "idea ideas brainstorm brainstorming thought thoughts proposal proposals what-if possibility possibilities",
    "solution": "solution solutions fix fixes answer answers resolution workaround sol",
    "question": "question questions q qs open-questions unknowns unknown wonder",
    "evidence": "evidence result results finding findings data observation observations experiment experiments",
    "decision": "decision decisions decided agreed conclusion conclusions",
    "assumption": "assumption assumptions assume assuming premise premises hypothesis hypotheses",
    "risk": "risk risks concern concerns issue issues problem problems limitation limitations caveat caveats challenge challenges",
    "explanation": "explanation explanations why rationale reasoning intuition because background",
    "reference": "reference references ref refs source sources citation citations paper papers reading literature link links",
    "task": "todo to-do todos task tasks next next-steps action actions action-items follow-up",
}
WORD_TO_KIND = {w: k for k, ws in KIND_WORDS.items() for w in ws.split()}

# Cue patterns: (kind, weight, regex). Scored on the lower-cased segment.
CUES = [
    ("question", 3, r"\?\s*$"),
    ("question", 2, r"\b(i wonder|wondering|unclear (whether|if|how|why|what)|not sure (whether|if|how|why|what|that)|open question|need to (find out|figure out|understand)|unknown whether|not clear (if|whether|how|why|what)|no idea (if|whether|how|why|what)|don'?t know (if|whether|how|why|what))\b"),
    ("idea", 4, r"\b(what if|how about)\b"),
    ("idea", 2, r"\b(idea|we could|could try|might try|how about|it would be (nice|cool|interesting|useful)|brainstorm|let'?s try|one option|alternatively|propose|proposal|we should (look|try|consider|explore|use)|should we|maybe (we|use|try|look)|instead|worth (trying|exploring|looking))\b"),
    ("solution", 2, r"\b(solution|solved|solves|solve it|fix(ed)?|resolv(e|ed|es)|workaround|the answer is|answer:|to address this|mitigat\w*|addressed by|turns out)\b"),
    ("method", 2, r"\b(method|methodology|approach|technique|algorithm|pipeline|procedure|protocol|framework|workflow|we use|we used|is done by|step \d|by (using|applying|training|computing))\b"),
    ("method", 1, r"\b(train(ed|ing)?|implement(ed|ing)?|appl(y|ied|ying)|estimat(e|ed|ing)|model(ed|led|ing)? (with|using|as))\b"),
    ("explanation", 2, r"\b(because|due to|this means|which means|that'?s why|the reason|in other words|so that|as a result|therefore|thus|hence|this is why|intuitively)\b"),
    ("decision", 3, r"\b(decided|decision|we will go with|we'?ll go with|going with|chose|chosen|choose to|agreed|settled on|we will use|we'?ll use)\b"),
    ("evidence", 2, r"\b(results? (show|shows|showed|indicate|suggest)|found that|we found|observed|measured|experiment(s|al)?|data (show|shows|suggest)|according to|significant(ly)?|study (shows|found)|outperform(s|ed)?)\b"),
    ("evidence", 1, r"\b(accuracy|benchmark|survey|baseline|ablation)\b"),
    ("evidence", 2, r"(\d+(\.\d+)?\s?%|\bp\s?[<=]\s?0?\.\d+|\(\s*[A-Z][\w\-]+(?: et al\.?)?,? \d{4}\s*\))"),
    ("assumption", 3, r"\b(assum\w*|presum\w*|suppose|supposing|given that|premise|we take for granted|hypothes\w*)\b"),
    # "people at risk" is a subject, not a project risk; "problem"/"issue" alone are weak signals
    ("risk", 1, r"\b(problem|issue)\b"),
    ("risk", 2, r"\b((?<!at )(?<!at-)risk(?!-?(?:score|stratif|assess|factor|group|prediction|model))\w*|concern\w*|danger\w*|might fail|could fail|may fail|bottleneck|limitation\w*|downside|threat|worr(y|ied)|caveat|pitfall|drawback|fragile|expensive|leak\w*|bias(ed)?|confound\w*|overfit\w*|too (slow|big|large|expensive|complex|noisy|small)|complain\w*|criticiz\w*|criticis\w*|doesn'?t scale|struggl\w*)\b"),
    ("task", 2, r"\b(todo|to-do|to do:|next step|need to|have to|must (check|read|write|ask|try|finish)|should (check|try|read|write|ask|look)|follow up|action item|remind me|don'?t forget)\b"),
    ("reference", 2, r"(https?://\S+|\b[A-Za-z]:[\\/]\S|\\\\\w|\bdoi:\s*\S+|\b10\.\d{4,9}/\S+|\barxiv[:\s]\s*\d{4}\.\d{4,5}|\bisbn[:\s])"),
]
CUES = [(k, w, re.compile(p, re.I)) for k, w, p in CUES]

KIND_PRIORITY = ["question", "decision", "solution", "evidence", "risk", "assumption", "method",
                 "explanation", "idea", "task", "reference"]

HEDGE_RE = re.compile(r"\b(maybe|might|perhaps|possibly|probably|i think|i guess|not sure|seems?|likely|could be|unclear|hypothes\w+|i suspect|i believe|guess)\b", re.I)
NEGATION_RE = re.compile(r"\b(not|never|fails?|failed|doesn'?t|don'?t|didn'?t|isn'?t|aren'?t|wasn'?t|cannot|can'?t|worse|no (improvement|effect|difference|gain)|contrary|refutes?|disproves?|unlike)\b", re.I)

# Relations: (type, regex, reverse). reverse=True means right --type--> left.
REL_SPECS = [
    ("depends on", r"depends? on|relies? on|requires?|needs|is built on|are built on|is contingent on|is conditional on", False),
    ("explains", r"explains?|accounts? for|is the reason for|causes?|leads? to|drives?|results? in", False),
    ("explains", r"is explained by|are explained by|is caused by|are caused by|is driven by|results? from|stems? from", True),
    ("supports", r"supports?|confirms?|validates?|corroborates?|is evidence for|backs? up|reinforces?|justifies", False),
    ("supports", r"is supported by|are supported by|is confirmed by|is validated by", True),
    ("contradicts", r"contradicts?|conflicts? with|is inconsistent with|are inconsistent with|refutes?|disproves?|undermines?|is at odds with|goes against", False),
    ("contradicts", r"is contradicted by|is refuted by", True),
    ("solves", r"solves?|fix(?:es)?|resolves?|addresses|mitigates?|overcomes?|eliminates?|prevents?|avoids?", False),
    ("solves", r"is solved by|is fixed by|is addressed by|is mitigated by|is resolved by|can be solved (?:with|by)", True),
    ("is an example of", r"is an? (?:example|instance|kind|type|form|case|variant|special case) of|are (?:examples|instances|kinds|types|forms|variants) of|is one of", False),
    ("is an example of", r"such as|e\.g\.,?|for example,?|for instance,?|including", True),
    ("derived from", r"(?:is |are )?derived from|(?:is |are )?based on|(?:is |are )?adapted from|(?:is |are )?inspired by|extends|builds? (?:up)?on|generali[sz]es", False),
]
REL_SPECS = [(t, re.compile(r"(?<![\w-])(?:" + p + r")(?![\w-])", re.I), rev) for t, p, rev in REL_SPECS]

EDGE_TYPES = ["depends on", "explains", "supports", "contradicts", "solves", "is an example of",
              "raises question", "derived from", "relates to"]

TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9'’\-+]*|\d+(?:\.\d+)?%?")
KEY_TOKEN_RE = re.compile(r"[A-Za-z0-9+#]+")   # same split as norm(): hyphens separate words
HANDLE_RE = re.compile(r"(?<![\w@])@([A-Za-z][\w\-]{0,40})")
TAG_RE = re.compile(r"(?<![\w#&])#([A-Za-z][\w\-]{0,40})")
WIKI_RE = re.compile(r"\[\[([^\]\n]{1,200})\]\]")
QUOTE_RE = re.compile(r"[\"“`]([^\"”`\n]{2,60})[\"”`]")
URL_RE = re.compile(r"https?://[^\s)>\]]+")
# Windows paths: quoted ones may contain spaces ("C:\My Papers\a.pdf"), bare ones may not.
PATH_RE = re.compile(r"\"((?:[A-Za-z]:[\\/]|\\\\)[^\"\n]+)\"|((?:[A-Za-z]:[\\/]|\\\\)[^\s\"<>|*?,;]+)")
LINK_RE = re.compile(URL_RE.pattern + "|" + PATH_RE.pattern)


def is_location(s: str) -> bool:
    """True for something that can be opened: a URL or a file/folder path."""
    s = (s or "").strip().strip('"')
    return bool(re.match(r"^(?:https?://|file:|[A-Za-z]:[\\/]|\\\\)", s))
DOI_RE = re.compile(r"\b(10\.\d{4,9}/[^\s,;)\]]+)")
ARXIV_RE = re.compile(r"\barxiv[:\s]\s*(\d{4}\.\d{4,5}(?:v\d+)?)", re.I)
CITE_RE = re.compile(r"\(([A-Z][A-Za-z\-]+(?: (?:et al\.?|and|&) ?[A-Z]?[A-Za-z\-]*)?,? (?:19|20)\d{2}[a-z]?)\)")
BULLET_RE = re.compile(r"^\s*(?:[-*•▪◦‣>+]+|\d{1,3}[.)]|[a-zA-Z][.)](?=\s))\s+")
MD_HEADING_RE = re.compile(r"^\s*#{1,6}\s+(.+?)\s*#*\s*$")
# "Q: ...", "Idea: ...", "Answer to Q-003: ...", "Evidence (K-002): ..."
PREFIX_RE = re.compile(r"^\s*(?:\*\*)?([A-Za-z][A-Za-z\- ]{0,20}?)(?:\*\*)?\s*(?:(?:for|to|on|about|re|of|against)\s+)?"
                       r"\(?([CMISQEDARXFTK]-\d{3,})?\)?\s*[:：]\s+(.*)$")
SENT_END_RE = re.compile(r"[.!?]+[\"”')\]]*\s+")
ABBREV = {"e.g", "i.e", "etc", "vs", "al", "fig", "eq", "approx", "cf", "dr", "mr", "mrs", "ms", "prof", "no", "vol", "sec", "ch"}
# a clause after ", but" / "; " / ", however" usually carries a separate point (an objection, a caveat)
CLAUSE_SPLIT_RE = re.compile(r"\s*(?:;|,?\s+but\s+|,\s*however,?\s+|,\s*although\s+|,?\s+whereas\s+|\s+-{1,2}\s+)", re.I)
FILLER_RE = re.compile(r"^(?:(?:so|ok(?:ay)?|anyway|also|and|plus|btw|basically|well|then|right|hmm+|oh|yeah|now)\b[,\s]*)+"
                       r"(?:(?:after|from|in) the [\w\s]{1,30}?,\s*)?(?:the main (?:thing|point|issue) is(?: that)?\s+)?", re.I)


def split_sentences(text: str) -> list[str]:
    """Sentence + clause split that also works for lower-case, chat-style writing."""
    out, start = [], 0
    for m in SENT_END_RE.finditer(text):
        prev = text[start:m.start()].rsplit(None, 1)
        word = prev[-1].lower().rstrip(".") if prev else ""
        if word in ABBREV or (len(word) == 1 and word.isalpha()):
            continue
        out.append(text[start:m.end()].strip())
        start = m.end()
    out.append(text[start:].strip())
    clauses = []
    for s in filter(None, out):
        parts = [p.strip() for p in CLAUSE_SPLIT_RE.split(s) if p and p.strip()]
        # only split when every piece is a real statement, otherwise keep the sentence whole
        if len(parts) > 1 and all(len(p.split()) >= 4 for p in parts):
            clauses.extend(parts)
        else:
            clauses.append(s)
    return clauses
DEF_IS_RE = re.compile(r"^(?:the\s+)?(?P<term>[A-Za-z][\w\-/+ ]{1,60}?)(?:\s*\((?P<abbr>[A-Za-z][\w\-]{1,12})\))?\s+(?:is|are)\s+(?P<def>(?:a|an|the|one of|when|where|how|what|any|basically|essentially|simply|just)\b.{3,})$", re.I)
DEF_VERB_RE = re.compile(r"^(?:the\s+)?(?P<term>[A-Za-z][\w\-/+ ]{1,60}?)(?:\s*\((?P<abbr>[A-Za-z][\w\-]{1,12})\))?\s+(?:is defined as|are defined as|refers to|refer to|means|is short for|stands for|denotes|describes)\s+(?P<def>.{3,})$", re.I)
DEF_COLON_RE = re.compile(r"^(?P<term>[A-Za-z][\w\-/+ ()]{1,50}?)\s*(?::|=|—|–|\s-\s)\s*(?P<def>.{3,})$")
ABBR_RE = re.compile(r"\b((?:[A-Z][a-z0-9\-]+|[a-z][a-z0-9\-]+)(?:\s+(?:[A-Z]?[a-z0-9\-]+)){1,6})\s+\(([A-Z][A-Za-z0-9]{1,9})\)")
AKA_RE = re.compile(r"(?P<a>[A-Za-z][\w\- ]{1,50}?)\s*,?\s*\(?(?:also known as|also called|aka|a\.k\.a\.|otherwise known as)\s+(?P<b>[A-Za-z][\w\- ]{1,50}?)\)?(?=[,.;)]|$)", re.I)
PRONOUN_START = set("it this that these those there we i you they he she which what who here one".split())


def singular(t: str) -> str:
    if len(t) > 4 and t.endswith("ies"):
        return t[:-3] + "y"
    if len(t) > 3 and t.endswith("s") and not t.endswith(("ss", "us", "is", "ys")):
        return t[:-1]
    return t


def norm(s: str) -> str:
    """Normalized key for matching names and aliases."""
    s = s.lower().replace("’", "'")
    s = re.sub(r"[^\w\s+#']", " ", s).replace("_", " ")
    toks = [singular(t.strip("'")) for t in s.split()]
    toks = [t for t in toks if t]
    while toks and toks[0] in ("the", "a", "an"):
        toks = toks[1:]
    return " ".join(toks)


def content_tokens(s: str) -> set[str]:
    return {singular(t.lower()) for t in TOKEN_RE.findall(s)
            if t.lower() not in STOP and len(t) > 2}


def jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def unwiki(s: str) -> str:
    """'[[DropEdge]] helps' -> 'DropEdge helps' (the note keeps the brackets; map items do not)."""
    return WIKI_RE.sub(lambda m: m.group(1), s or "")


def tidy(s: str) -> str:
    s = re.sub(r"\s+", " ", s).strip()
    return s.strip(" -–—*•:;,")


def short_title(s: str, n: int = 90) -> str:
    s = tidy(HANDLE_RE.sub("", s))
    stripped = tidy(FILLER_RE.sub("", s))
    if len(stripped.split()) >= 3:
        s = stripped[0].upper() + stripped[1:]
    if len(s) <= n:
        return s
    cut = s[:n].rsplit(" ", 1)[0]
    return cut + "…"


def acronym_of(phrase: str) -> str:
    return "".join(w[0] for w in re.split(r"[\s\-]+", phrase) if w and w.lower() not in ("of", "and", "the", "for", "in", "on")).upper()


def _is_acronym(s: str) -> bool:
    s = s.strip()
    return " " not in s and 2 <= len(s) <= 8 and sum(c.isupper() for c in s) >= 2 and s[0].isupper()


def _expands(acr: str, phrase: str) -> bool:
    """Does `phrase` spell out `acr`? (DSL ~ Domain-Specific Language, ANX ~ AIGENT Nexus: first letters overlap)"""
    a, p = acr.upper().rstrip("S"), acronym_of(phrase.rstrip("."))
    return bool(p) and (p == a or p.startswith(a[:2]) or a.startswith(p[:2]))


# ---------------------------------------------------------------- data shapes

@dataclass
class Mention:
    text: str
    strength: str          # explicit | known | strong | weak
    start: int = -1
    end: int = -1


@dataclass
class Relation:
    left: str | None       # None -> the segment's own statement node
    type: str
    right: str | None
    sentence: str


@dataclass
class Segment:
    idx: int
    text: str
    kind: str              # one of NODE_KINDS or "" (no statement)
    kind_source: str       # prefix | heading | cue | default | term
    confidence: str        # high | medium | low
    is_term: bool = False  # short label (becomes a named node, e.g. a method name)
    negated: bool = False
    code_refs: list[str] = field(default_factory=list)
    target_ref: str | None = None   # "(Q-003)" style target in a prefix
    parent: int | None = None       # index of the "...:" statement that introduced this list item
    mentions: list[Mention] = field(default_factory=list)
    chunks: list[str] = field(default_factory=list)
    relations: list[Relation] = field(default_factory=list)
    cue_hits: dict = field(default_factory=dict)
    alt_kinds: list[str] = field(default_factory=list)  # two cue kinds scored equally (an ambiguity)


@dataclass
class ParsedNote:
    text: str
    handles: list[str]
    tags: list[str]
    segments: list[Segment]
    definitions: list[tuple[str, str, int]]       # term, definition, segment idx
    aliases: list[tuple[str, str]]                 # term, alias
    references: list[tuple[str, str, int]]         # kind (url/doi/arxiv/cite), value, segment idx
    ambiguities: list[str]


# ---------------------------------------------------------------- helpers

def _kind_from_label(label: str) -> str | None:
    w = label.strip().lower().replace(" ", "-").rstrip("s") if label else ""
    for cand in (label.strip().lower().replace(" ", "-"), w, label.strip().lower().split(" ")[0] if label else ""):
        if cand in WORD_TO_KIND:
            return WORD_TO_KIND[cand]
    return None


def _score_kinds(text: str) -> dict[str, int]:
    scores: dict[str, int] = {}
    for kind, w, rx in CUES:
        if rx.search(text):
            scores[kind] = scores.get(kind, 0) + w
    low = text.lower().strip()
    first = low.split(" ", 1)[0] if low else ""
    if first in ("why", "how", "what", "which", "who", "when", "where", "whether") and "." not in low[:-1] \
            and not low.startswith(("what if", "how about")):
        scores["question"] = scores.get("question", 0) + 1
    return scores


def _pick_kind(scores: dict[str, int]) -> str | None:
    if not scores:
        return None
    best = max(scores.values())
    for k in KIND_PRIORITY:
        if scores.get(k) == best:
            return k
    return None


def _np_backward(text: str) -> str | None:
    toks = list(TOKEN_RE.finditer(text))
    out = []
    for m in reversed(toks):
        t = m.group(0)
        tl = t.lower()
        if tl in STOP or tl in VERBS or tl in DETERMINERS:
            break
        out.append(t)
        if len(out) >= 4:
            break
    if not out:
        return None
    return " ".join(reversed(out))


def _np_forward(text: str) -> str | None:
    toks = [m.group(0) for m in TOKEN_RE.finditer(text)]
    i = 0
    while i < len(toks) and toks[i].lower() in DETERMINERS:
        i += 1
    out = []
    while i < len(toks) and len(out) < 4:
        t = toks[i]
        tl = t.lower()
        if tl == "of" and out and i + 1 < len(toks) and toks[i + 1].lower() not in STOP:
            out.append(t)
            i += 1
            continue
        if tl in STOP or tl in VERBS:
            break
        out.append(t)
        i += 1
    if out and out[-1].lower() == "of":
        out.pop()
    return " ".join(out) if out else None


def _good_term(term: str) -> bool:
    words = term.split()
    if not words or len(words) > 6:
        return False
    if words[0].lower() in PRONOUN_START or words[0].lower() in STOP - {"a", "an", "the"}:
        return False
    if not any(len(w) >= 3 for w in words):
        return False
    if all(w.lower() in STOP for w in words):
        return False
    return True


def _find_known(text: str, known: set[str]) -> list[Mention]:
    """Longest-match lookup of existing node names/aliases inside text."""
    if not known:
        return []
    toks = [(m.start(), m.end(), singular(m.group(0).lower())) for m in KEY_TOKEN_RE.finditer(text)]
    found, i = [], 0
    while i < len(toks):
        hit = None
        for n in range(min(6, len(toks) - i), 0, -1):
            key = " ".join(t[2] for t in toks[i:i + n])
            if key in known and (n > 1 or len(key) >= 3 or key.isupper()):
                hit = (n, key)
                break
        if hit:
            n, _ = hit
            s, e = toks[i][0], toks[i + n - 1][1]
            found.append(Mention(text[s:e], "known", s, e))
            i += n
        else:
            i += 1
    return found


def _cap_phrases(text: str) -> list[Mention]:
    out = []
    toks = list(TOKEN_RE.finditer(text))
    joiners = {"of", "for", "de"}
    i = 0
    while i < len(toks):
        t = toks[i].group(0)
        if t[0].isupper() and t not in CAP_STOP and not t.isupper():
            j = i
            run = [i]
            while j + 1 < len(toks):
                nxt = toks[j + 1].group(0)
                gap = text[toks[j].end():toks[j + 1].start()]
                if gap.strip() not in ("", "-"):
                    break
                if nxt[0].isupper() and nxt not in CAP_STOP:
                    run.append(j + 1)
                    j += 1
                elif nxt.lower() in joiners and j + 2 < len(toks) and toks[j + 2].group(0)[0].isupper() \
                        and text[toks[j + 1].end():toks[j + 2].start()].strip() == "":
                    run.extend([j + 1, j + 2])
                    j += 2
                else:
                    break
            s, e = toks[run[0]].start(), toks[run[-1]].end()
            at_start = text[:s].strip() == "" or text[:s].rstrip()[-1:] in ".!?:;\"“(-•*"
            if len(run) >= 2:
                out.append(Mention(text[s:e], "strong", s, e))
            elif not at_start and len(t) >= 3:
                out.append(Mention(t, "weak", s, e))
            i = j + 1
        else:
            i += 1
    return out


def _acronyms(text: str) -> list[Mention]:
    out = []
    for m in TOKEN_RE.finditer(text):
        t = m.group(0).rstrip("s") if m.group(0).endswith("s") and sum(c.isupper() for c in m.group(0)) >= 2 else m.group(0)
        ups = sum(c.isupper() for c in t)
        if ups >= 2 and len(t) <= 10 and t.upper() not in ACRO_STOP and not t.isdigit() and t[0].isupper():
            out.append(Mention(m.group(0), "strong", m.start(), m.end()))
    return out


def _chunks(text: str) -> list[str]:
    """Lower-case 2-3 word noun-ish chunks; promoted to concepts when they recur."""
    out, run = [], []
    prev_end = 0
    for m in TOKEN_RE.finditer(text):
        t = m.group(0)
        tl = t.lower()
        if re.search(r"[,;:()\[\]/]", text[prev_end:m.start()]) and run:   # punctuation ends a phrase
            if 2 <= len(run) <= 3:
                out.append(" ".join(run))
            elif len(run) > 3:
                out.append(" ".join(run[-3:]))
            run = []
        prev_end = m.end()
        ok = tl not in STOP and tl not in VERBS and tl not in CHUNK_STOP and t.islower() and len(t) >= 3 \
            and t.isalpha() and not tl.endswith(("ly",))
        if ok:
            run.append(tl)
        else:
            if 2 <= len(run) <= 3:
                out.append(" ".join(run))
            elif len(run) > 3:
                out.append(" ".join(run[-3:]))
            run = []
    if 2 <= len(run) <= 3:
        out.append(" ".join(run))
    elif len(run) > 3:
        out.append(" ".join(run[-3:]))
    return out


def _overlaps(a: Mention, b: Mention) -> bool:
    return a.start < b.end and b.start < a.end


def _relations(sentence: str, mentions: list[Mention], known: set[str] = frozenset()) -> list[Relation]:
    rels = []

    def specific(np):
        # a lone lower-case word ("users", "models") is too generic to become a concept from a relation
        return np and (len(np.split()) > 1 or not np.islower() or norm(np) in known)
    for rtype, rx, rev in REL_SPECS:
        for m in rx.finditer(sentence):
            before, after = sentence[:m.start()], sentence[m.end():]
            left = None
            cands = [x for x in mentions if x.end <= m.start() and len(TOKEN_RE.findall(sentence[x.end:m.start()])) <= 2]
            if cands:
                left = max(cands, key=lambda x: x.end).text
            else:
                left = _np_backward(before)
            right = None
            cands = [x for x in mentions if x.start >= m.end() and len(TOKEN_RE.findall(sentence[m.end():x.start])) <= 2]
            if cands:
                right = min(cands, key=lambda x: x.start).text
            else:
                right = _np_forward(after)
            if right and not (_good_term(right) and specific(right)):
                right = None
            if left and not (_good_term(left) and specific(left)):
                left = None
            if not right and not left:
                continue
            if rev:
                if not left or not right:
                    continue
                left, right = right, left
            if left and right and norm(left) == norm(right):
                continue
            rels.append(Relation(left, rtype, right, tidy(sentence)))
    return rels


# ---------------------------------------------------------------- main entry

# Lines that only announce what follows; they carry no content of their own.
GENERIC_HEADINGS = {"plain-english explanation", "plain english explanation", "in one sentence", "in short",
                    "summary", "tl;dr", "tldr", "overview", "explanation", "example", "examples", "for example",
                    "in other words", "background", "introduction", "conclusion", "notes", "note", "context",
                    "details", "key points", "takeaways", "key takeaways", "in summary", "to summarize", "why it matters"}
GLUED_HEADER_RE = re.compile(r"^[A-Z][a-z]+(?=[A-Z]{2,}\b)")   # "PurposeETN" -> "ETN" (table header pasted onto row 1)


def split_segments(text: str) -> list[tuple[str, str | None, str, str | None, int | None]]:
    """Return (segment_text, kind_hint, hint_source, target_ref, list_parent_index)."""
    out = []
    context: str | None = None
    bullets_seen = False
    list_parent: int | None = None     # a "...as follows:" statement whose items come next
    blank_run = 0
    table_rows = 0
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip():
            blank_run += 1
            context = None
            if blank_run > 1:
                list_parent = None
            table_rows = 0
            continue
        blank_run = 0
        # pasted table row (cells separated by tabs): "Term <tab> explanation" becomes a definition
        if "\t" in line:
            cells = [c.strip() for c in line.split("\t") if c.strip()]
            if len(cells) >= 2:
                table_rows += 1
                if table_rows == 1 and all(len(c.split()) <= 3 for c in cells):
                    continue                                   # header row
                term = GLUED_HEADER_RE.sub("", cells[-2])
                out.append((f"{term}: {cells[-1]}", "concept", "table", None, None))
                continue
        stripped = BULLET_RE.sub("", line).strip()
        if stripped.lower().rstrip(":").strip() in GENERIC_HEADINGS:
            continue
        h = MD_HEADING_RE.match(line)
        if h:
            context, bullets_seen = _kind_from_label(h.group(1)), False
            title = h.group(1).strip()
            if context is None and len(title.split()) <= 8:
                out.append((title, "concept", "term", None, None))
            continue
        bullet = bool(BULLET_RE.match(line))
        body = BULLET_RE.sub("", line).strip()
        # A short line ending with ':' acts as a heading for what follows.
        if body.endswith(":") and not bullet and (len(body.split()) <= 2 or _kind_from_label(body[:-1])):
            context, bullets_seen = _kind_from_label(body[:-1]), False
            if context is None and out:
                list_parent = len(out) - 1    # "...such as:" continues the previous statement
            continue
        # items of a list introduced by "...:" belong to that statement; a long plain line ends the list
        parent = None
        if list_parent is not None:
            if bullet or len(body.split()) <= 6:
                parent = list_parent
            else:
                list_parent = None
        # A heading's context covers its bullet list; a plain line after the list ends it.
        if bullet:
            bullets_seen = True
        elif bullets_seen:
            context, bullets_seen = None, False
        hint, src, target = context, "heading" if context else None, None
        # Trailing hashtags that name a kind (#todo, #question, #idea ...) act as a prefix.
        trail = re.search(r"(?:\s+#[A-Za-z][\w\-]*)+\s*$", body)
        if trail:
            for t in reversed(TAG_RE.findall(trail.group(0))):
                k = _kind_from_label(t)
                if k:
                    hint, src = k, "prefix"
                    break
            body = body[:trail.start()].rstrip()
        pm = PREFIX_RE.match(body)
        if pm:
            k = _kind_from_label(pm.group(1))
            if k:
                hint, src, target = k, "prefix", pm.group(2)
                body = pm.group(3).strip()
            elif pm.group(1).lower() in ("note", "notes", "nb", "fyi", "thought"):
                body = pm.group(3).strip()
        sentences = split_sentences(body) or [body]
        for s in sentences:
            out.append((s.strip().rstrip(",;"), hint, src or "", target, parent))
        if body.endswith(":") and parent is None:
            list_parent = len(out) - 1
    return out


def parse(text: str, known: set[str] | None = None) -> ParsedNote:
    """Two passes: names introduced anywhere in the note are recognised everywhere in it."""
    known = set(known or ())
    first = _parse(text, known)
    local = set()
    for s in first.segments:
        for m in s.mentions:
            if m.strength in ("explicit", "strong", "known"):
                local.add(norm(m.text))
        if s.is_term:
            local.add(norm(s.text))
        for r in s.relations:
            local.update(norm(x) for x in (r.left, r.right) if x)
    for a, b in first.aliases:
        local.update((norm(a), norm(b)))
    local.discard("")
    if local - known:
        return _parse(text, known | local)
    return first


def _parse(text: str, known: set[str]) -> ParsedNote:
    handles = list(dict.fromkeys(h.lower() for h in HANDLE_RE.findall(text)))
    tags = list(dict.fromkeys(t.lower() for t in TAG_RE.findall(text)))
    definitions, aliases, references, ambiguities = [], [], [], []
    segments: list[Segment] = []
    seen_refs = set()

    for idx, (seg_text, hint, hint_src, target, parent) in enumerate(split_segments(text)):
        clean = tidy(TAG_RE.sub(lambda m: m.group(1), HANDLE_RE.sub("", seg_text)))
        if not clean:
            continue
        # a short quoted line on its own is a title/term: “Automatic coupling to simulators and data sources”
        qt = re.fullmatch(r"[\"“]([^\"“”]{2,90})[\"”]", clean)
        if qt and len(qt.group(1).split()) <= 10 and not re.search(r"[.!?]$", qt.group(1)) and not hint:
            clean, hint, hint_src = qt.group(1).strip(), "concept", "term"
        # text with links removed, used for concept/chunk extraction
        bare = LINK_RE.sub(lambda m: " " * len(m.group(0)), clean)
        words = clean.split()
        scores = _score_kinds(clean)
        cue_kind = _pick_kind(scores)

        kind, src = None, ""
        if hint_src == "prefix" and hint:
            kind, src = hint, "prefix"
        elif hint and hint_src == "heading":
            # heading context wins unless a strong cue says otherwise (e.g. a '?' line)
            kind, src = hint, "heading"
            if cue_kind == "question" and scores.get("question", 0) >= 3:
                kind, src = "question", "cue"
        elif hint and hint_src in ("term", "table"):
            kind, src = hint, hint_src
        elif cue_kind:
            kind, src = cue_kind, "cue"

        is_term = False
        term_like = len(words) <= (10 if src == "term" else 6) and not re.search(r"[.!?;]$", clean) \
            and not scores.get("question")
        if src in ("heading", "term") and term_like and not DEF_COLON_RE.match(clean):
            is_term = True

        seg = Segment(idx=idx, text=clean, kind=kind or "", kind_source=src, confidence="medium",
                      is_term=is_term, target_ref=target, cue_hits=scores, parent=parent)
        seg.code_refs = ["%s-%s" % m for m in CODE_RE.findall(seg_text)]
        if target:
            seg.code_refs.append(target)
        if HEDGE_RE.search(clean):
            seg.confidence = "low"
        elif src == "prefix":
            seg.confidence = "high"
        seg.negated = bool(NEGATION_RE.search(clean))

        # --- definitions and aliases
        d = None
        # allow a quoted term: “Constrained” means …
        dclean = re.sub(r"^[\"“‘']([^\"“”‘’']{2,60}?)[\"”’']\s+", r"\1 ", clean)
        for rx in (DEF_VERB_RE, DEF_IS_RE):
            d = rx.match(dclean)
            if d:
                break
        # "X is an example of Y" is a relation, not a definition
        if d and re.match(r"(?:an?|one)\s+(?:example|instance|kind|type|form|case|variant|special case)\s+of\b",
                          d.group("def"), re.I):
            d = None
        if not d and (kind == "concept" or src == "") and not LINK_RE.search(clean):
            d = DEF_COLON_RE.match(clean)
            if d and _kind_from_label(d.group("term")):
                d = None
        if d:
            term = tidy(d.group("term"))
            abbr = d.groupdict().get("abbr")
            definition = tidy(d.group("def"))
            m2 = re.match(r"^(.*?)\s*\(([^()]{2,60})\)$", term)
            if m2:
                outer, inner = m2.group(1).strip(), m2.group(2).strip()
                if " " not in inner and len(inner) <= 12:          # Long Name (LN)
                    term, abbr = outer, inner
                elif _is_acronym(outer):                          # LN (Long Name): long form is the name
                    term, abbr = inner, outer
            if _is_acronym(term) and len(definition.split()) <= 6 and _expands(term, definition):
                # "DSL = Domain-Specific Language" names the acronym; it does not define it
                aliases.append((definition.rstrip("."), term))
                seg.mentions.append(Mention(definition.rstrip("."), "explicit"))
                seg.kind, seg.kind_source, seg.is_term = "", "definition", False
                d = None
        if d:
            if _good_term(term):
                definitions.append((term, definition, idx))
                seg.mentions.append(Mention(term, "explicit", 0, len(term)))
                if abbr:
                    aliases.append((term, abbr))
                if not kind or src in ("", "term") or kind == "concept":
                    seg.kind, seg.kind_source = "", "definition"
                seg.is_term = False
        for m in ABBR_RE.finditer(clean):
            phrase, abbr = m.group(1), m.group(2)
            words_p = phrase.split()
            # trim the phrase to the words the acronym actually covers
            n = len(abbr.rstrip("s"))
            if n <= len(words_p):
                cand = " ".join(words_p[-n:])
                if acronym_of(cand).startswith(abbr.rstrip("s").upper()[:2]):
                    phrase = cand
            if _good_term(phrase):
                aliases.append((tidy(phrase), abbr))
                seg.mentions.append(Mention(tidy(phrase), "strong", m.start(1), m.end(1)))
        for m in AKA_RE.finditer(clean):
            a = _np_backward(m.group("a")) or tidy(m.group("a"))
            b = tidy(m.group("b"))
            if _good_term(a) and _good_term(b):
                aliases.append((a, b))

        # --- references
        for rx, rkind in ((URL_RE, "url"), (PATH_RE, "path"), (DOI_RE, "doi"), (ARXIV_RE, "arxiv"), (CITE_RE, "cite")):
            for m in rx.finditer(seg_text):
                val = next((g for g in m.groups() if g), m.group(0)) if rx.groups else m.group(0)
                val = val.rstrip(".,;")
                if val.lower() not in seen_refs:
                    seen_refs.add(val.lower())
                    references.append((rkind, val, idx))
        if kind == "reference" and not references:
            references.append(("text", clean, idx))

        # --- concept mentions
        ms: list[Mention] = []
        for m in WIKI_RE.finditer(bare):
            ms.append(Mention(m.group(1).strip(), "explicit", m.start(1), m.end(1)))
        for m in QUOTE_RE.finditer(bare):
            q = m.group(1).strip()
            if _good_term(q) and len(q.split()) <= 6:
                ms.append(Mention(q, "explicit", m.start(1), m.end(1)))
        ms += _find_known(bare, known)
        ms += _acronyms(bare)
        ms += _cap_phrases(bare)
        # keep the strongest, longest, non-overlapping mentions
        rank = {"explicit": 3, "known": 2, "strong": 1, "weak": 0}
        ms.sort(key=lambda x: (-rank[x.strength], -(x.end - x.start)))
        kept: list[Mention] = []
        for m in ms:
            if m.start >= 0 and any(_overlaps(m, k) for k in kept if k.start >= 0):
                continue
            if not _good_term(m.text) and m.strength != "explicit" and not m.text.isupper():
                continue
            kept.append(m)
        seg.mentions += sorted(kept, key=lambda x: x.start)
        # dedupe by norm
        uniq, seen = [], set()
        for m in seg.mentions:
            k = norm(m.text)
            if k and k not in seen:
                seen.add(k)
                uniq.append(m)
        seg.mentions = uniq

        seg.relations = _relations(bare, [m for m in seg.mentions if m.start >= 0], known)
        if not seg.is_term:
            seg.chunks = _chunks(bare)

        # --- statement kind fallback
        if not seg.kind and seg.kind_source != "definition":
            if len(words) >= 4:
                seg.kind, seg.kind_source = "claim", "default"
            elif seg.mentions or len(words) >= 1:
                seg.kind, seg.kind_source, seg.is_term = "concept", "term", True
        if seg.is_term and seg.kind in ("question",):
            seg.is_term = False

        # ambiguity: two strong competing cue kinds
        top = sorted(scores.items(), key=lambda kv: -kv[1])
        if seg.kind_source == "cue" and len(top) >= 2 and top[0][1] == top[1][1] and top[0][1] >= 2:
            ambiguities.append(f"“{short_title(clean, 60)}” reads as {top[0][0]} or {top[1][0]}; filed as {seg.kind}.")
            seg.alt_kinds = [top[0][0], top[1][0]]
        segments.append(seg)

    aliases = list(dict.fromkeys(aliases))
    return ParsedNote(text=text, handles=handles, tags=tags, segments=segments,
                      definitions=definitions, aliases=aliases, references=references,
                      ambiguities=ambiguities)
