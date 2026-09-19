"""
learning.py - what the system does with the corrections it has been given.

There is no fine-tuning here and there should not be: a handful of expert edits
is far too little to train on, and retraining would bury the reviewer's
reasoning inside weights where nobody can inspect or withdraw it. Instead the
corrections stay as rows, and the closest ones are handed back to the model as
evidence at the moment it reads a new song. Retire a correction and its
influence disappears on the next request.

Two levels of recall:

  exact      the same lyrics have been corrected before, so serve that
             correction instead of asking the model again
  similar    a nearby song has been corrected, so append the reviewer's
             changes and reasoning to the input as guidance

Similarity is cosine distance between embeddings of the composed input. If the
embedding service is unavailable the module degrades to exact matching only and
says so, rather than failing the request.
"""

import json
import math
import os

import Database

# --- tuning knobs --------------------------------------------------------

THRESHOLD = float(os.getenv("LYRIQ_MATCH_THRESHOLD", "0.82"))
MAX_MATCHES = int(os.getenv("LYRIQ_MAX_MATCHES", "3"))

# Fields worth comparing between the model's verdict and the expert's. Anything
# outside this list is stored but not turned into a teaching line.
WATCHED = [
    "valence",
    "arousal",
    "quadrant",
    "primary_emotion",
    "secondary_emotions",
    "mixed_emotion",
    "rasa",
    "parjaay",
    "tradition",
    "language",
    "confidence",
    "summary",
    "music_therapy",
    "recommendation_tags",
]

# Long free text is summarised rather than quoted in full when teaching.
LONG_FIELDS = {"summary", "music_therapy"}

GUIDANCE_HEADER = (
    "\n\n=== Reviewer corrections on similar songs ===\n"
    "A human expert reviewed earlier readings of songs close to this one and "
    "changed them as listed below. Treat this as evidence about how this "
    "reviewer reads this repertoire, not as facts about the present song. "
    "Apply the same reasoning where it fits the words in front of you, and "
    "ignore it where it does not. Do not copy a previous verdict onto a "
    "different song, and do not mention these notes in your output.\n"
)


# --- embeddings ----------------------------------------------------------

_embedder = None


def _get_embedder():
    """Built on first use so importing this module never needs an API key."""
    global _embedder
    if _embedder is None:
        from langchain_google_genai import GoogleGenerativeAIEmbeddings
        _embedder = GoogleGenerativeAIEmbeddings(model="models/gemini-embedding-004")
    return _embedder


def embed(text):
    """Vector for one text, or None if the service is not reachable."""
    try:
        return _get_embedder().embed_query(text)
    except Exception:  # noqa: BLE001 - embeddings are an optimisation, not a requirement
        return None


def _cosine(a, b):
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    left = math.sqrt(sum(x * x for x in a))
    right = math.sqrt(sum(y * y for y in b))
    if left == 0 or right == 0:
        return 0.0
    return dot / (left * right)


# --- diffing -------------------------------------------------------------

def _comparable(value):
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:.2f}"
    if value is None:
        return ""
    return str(value).strip()


def _normalise(field, value):
    """Flatten differences of format so they are not mistaken for judgements.

    analyze() returns quadrant as "Q3 - sad / depressed" while the editor sends
    back "Q3". That is the same verdict written two ways, and teaching it as a
    correction would fill the guidance block with noise.
    """
    text = _comparable(value)
    if field == "quadrant":
        return text[:2].upper()
    if field in ("secondary_emotions", "recommendation_tags"):
        return ", ".join(sorted(part.strip().lower() for part in text.split(",") if part.strip()))
    if field in ("primary_emotion", "rasa", "parjaay", "tradition", "language"):
        return text.strip().lower()
    return text


_EMPTY = {"", "no", "0.00", "none", "null"}


def changes(original, corrected):
    """Which watched fields the expert actually moved, and from what to what."""
    original = original or {}
    corrected = corrected or {}
    out = {}
    for field in WATCHED:
        before = _normalise(field, original.get(field))
        after = _normalise(field, corrected.get(field))
        if before == after:
            continue
        # A field the model never produced, arriving empty from the form, is
        # the form filling a blank rather than the reviewer deciding anything.
        if field not in original and after.lower() in _EMPTY:
            continue
        out[field] = {
            "from": _comparable(original.get(field)),
            "to": _comparable(corrected.get(field)),
        }
    return out


# --- writing -------------------------------------------------------------

def remember(analysis_id, corrected, editor=None, note=None):
    """Store one expert edit against the reading it corrects.

    Returns the correction row. The embedding is computed here, once, so that
    reading time stays a single vector comparison over rows already in memory.
    """
    analysis = Database.get_analysis(analysis_id)
    if analysis is None:
        raise ValueError(f"No analysis with id {analysis_id}.")

    original = analysis["output"]
    diff = changes(original, corrected)

    correction_id = Database.save_correction(
        analysis_id=analysis_id,
        original=original,
        corrected=corrected,
        changed=diff,
        editor=editor,
        note=note,
        embedding=embed(analysis["input_text"]),
    )
    return Database.get_correction(correction_id)


# --- reading -------------------------------------------------------------

def exact_correction(text):
    """A correction for byte-identical input, or None."""
    return Database.correction_for(text)


def similar_corrections(text, limit=MAX_MATCHES, threshold=THRESHOLD):
    """The nearest corrections to this song, best first."""
    pool = Database.active_corrections(with_vector=True)
    if not pool:
        return []

    vector = embed(text)
    if vector is None:
        return []

    scored = []
    for correction in pool:
        score = _cosine(vector, correction.get("embedding"))
        if score >= threshold:
            correction = dict(correction)
            correction["similarity"] = round(score, 3)
            correction.pop("embedding", None)
            scored.append(correction)

    scored.sort(key=lambda c: c["similarity"], reverse=True)
    return scored[:limit]


def _teaching_lines(correction):
    lines = []
    for field, move in (correction.get("changed") or {}).items():
        before, after = move.get("from", ""), move.get("to", "")
        if field in LONG_FIELDS:
            lines.append(f"  {field}: the reviewer rewrote this. Theirs reads: {after[:240]}")
        else:
            lines.append(f"  {field}: you said {before or 'nothing'}, "
                         f"the reviewer set {after or 'nothing'}")
    if correction.get("note"):
        lines.append(f"  their reasoning: {correction['note']}")
    return lines


def guidance_for(text, limit=MAX_MATCHES, threshold=THRESHOLD):
    """Build the block appended to the analyser's input.

    Returns (block, matches). block is "" when there is nothing to teach, which
    means a lyrics-only request reaches analyze() exactly as it does today.
    """
    matches = similar_corrections(text, limit, threshold)
    if not matches:
        return "", []

    blocks = []
    for index, correction in enumerate(matches, start=1):
        lines = _teaching_lines(correction)
        if not lines:
            continue
        head = (f"\nSimilar song {index}, similarity {correction['similarity']}, "
                f"opening {correction['excerpt'][:70]}")
        blocks.append(head + "\n" + "\n".join(lines))

    if not blocks:
        return "", []

    return GUIDANCE_HEADER + "\n".join(blocks) + "\n=== end of reviewer corrections ===\n", matches


def summarise(matches):
    """Small shape for the browser, so the page can say what it leaned on."""
    return [
        {
            "correction_id": match["id"],
            "similarity": match["similarity"],
            "excerpt": match["excerpt"],
            "fields": list((match.get("changed") or {}).keys()),
            "note": match.get("note"),
        }
        for match in matches
    ]


def health():
    """Whether learned mode can do anything useful right now."""
    counts = Database.stats()
    counts["embeddings_available"] = embed("test") is not None
    counts["threshold"] = THRESHOLD
    return counts