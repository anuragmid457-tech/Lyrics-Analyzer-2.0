"""
app.py - HTTP layer only.

No prompt, no model, no label logic lives here. Both live in your two
analyser files:
  lyrics_senti_analysis.py  -> analyze(lyrics: str) -> dict
  lyrics_text.py            -> detect_emotion(path) -> str   (reads a PDF)

Three modules sit beside them:
  graph.py      section by section scores, shaped as Plotly figures
  learning.py   what past expert corrections should tell the analyser
  review.py     the expert-facing routes, registered as a blueprint
  database.py   storage, and the only file that touches SQLite
"""

import os
import tempfile

from flask import Flask, jsonify, render_template, request
from werkzeug.utils import secure_filename

import Database
import graph
import Learning
import lyrics_senti_analysis
import lyrics_text
from Review import review

app = Flask(__name__)
app.register_blueprint(review)

Database.init()

# --- optional inputs -----------------------------------------------------
# The browser sends lyrics plus any of these. Blank ones are dropped, so a
# lyrics-only request reaches analyze() as bare lyrics and nothing else.
OPTIONAL = [
    ("title", "Title"),
    ("composer", "Composer or lyricist"),
    ("tradition", "Tradition"),
    ("language", "Language"),
    ("parjaay", "Parjaay"),
    ("raga", "Raga or scale"),
    ("taal", "Taal"),
    ("laya", "Laya or tempo"),
    ("notation", "Notation"),
    ("notes", "Notes"),
]
MULTILINE = {"notation", "notes"}


def compose(payload):
    """Fold whatever optional fields were filled in into one text block."""
    lyrics = payload["lyrics"].strip()
    extras = []
    for key, label in OPTIONAL:
        value = (payload.get(key) or "").strip()
        if not value:
            continue
        extras.append(f"{label}:\n{value}" if key in MULTILINE else f"{label}: {value}")
    if not extras:
        return lyrics
    return lyrics + "\n\n---\nSupplied alongside the lyrics:\n" + "\n".join(extras)


def learned_mode(payload):
    """Per-request choice if the browser sent one, otherwise the stored default."""
    if isinstance(payload.get("use_learned"), bool):
        return payload["use_learned"]
    return Database.get_preference("use_learned", "1") == "1"


# --- response shaping ----------------------------------------------------

QUADRANTS = {
    "Q1": "Q1 · happy, excited",
    "Q2": "Q2 · tense, agitated",
    "Q3": "Q3 · sad, subdued",
    "Q4": "Q4 · calm, serene",
}


def for_browser(result):
    """Light touch-up of your JSON so the page can render it.

    Pulls the Qn code out of a longer quadrant string, falls back to deriving
    it from the coordinates, and accepts either music_therapy key. Everything
    else is passed through untouched.
    """
    out = dict(result)

    code = str(out.get("quadrant") or "")[:2].upper()
    if code not in QUADRANTS:
        valence, arousal = out.get("valence") or 0, out.get("arousal") or 0
        code = ("Q1" if arousal >= 0 else "Q4") if valence >= 0 else ("Q2" if arousal >= 0 else "Q3")
    out["quadrant"] = code
    out["quadrant_label"] = QUADRANTS[code]

    if "music_therapy_context" not in out and "music_therapy" in out:
        out["music_therapy_context"] = out["music_therapy"]

    out.setdefault("status", "ok")
    return out


# --- routes --------------------------------------------------------------

@app.get("/")
def index():
    return render_template("index.html")


@app.post("/api/analyze")
def api_analyze():
    payload = request.get_json(silent=True) or {}
    if not (payload.get("lyrics") or "").strip():
        return jsonify({"error": "Paste some lyrics to analyse."}), 400

    text = compose(payload)
    use_learned = learned_mode(payload)

    # An identical song corrected before is served from that correction rather
    # than asked again. Turn learned mode off to see what the model says alone.
    if use_learned:
        hit = Learning.exact_correction(text)
        if hit:
            out = for_browser(hit["corrected"])
            out["analysis_id"] = Database.save_analysis(
                text, hit["corrected"], source="correction",
                learned_from=[hit["id"]],
            )
            out["learning"] = {
                "mode": "learned",
                "source": "correction",
                "correction_id": hit["id"],
                "edited_at": hit["created_at"],
                "editor": hit["editor"],
                "matches": [],
            }
            return jsonify(out)

    guidance, matches = Learning.guidance_for(text) if use_learned else ("", [])

    try:
        result = lyrics_text.analyze(text + guidance)
    except Exception as exc:  # noqa: BLE001 - show the real cause in the UI
        return jsonify({"error": f"{type(exc).__name__}: {exc}"}), 500

    if not isinstance(result, dict):
        return jsonify({"error": "analyze() returned something other than a dict."}), 500
    if result.get("parse_error"):
        return jsonify({
            "error": "The model did not return valid JSON.",
            "raw": result.get("raw_output", ""),
        }), 502

    out = for_browser(result)
    out["analysis_id"] = Database.save_analysis(
        text, result, source="model",
        learned_from=[match["id"] for match in matches] or None,
    )
    out["learning"] = {
        "mode": "learned" if use_learned else "default",
        "source": "guided" if matches else "model",
        "matches": Learning.summarise(matches),
    }
    return jsonify(out)


@app.post("/api/graph")
def api_graph():
    """Section-by-section scores for the same lyrics, shaped as Plotly figures.

    Takes the same payload /api/analyze takes, plus an optional "analysis"
    object: pass the dict you already got back from /api/analyze and the
    whole-song verdict is plotted alongside the sections as a single star.
    """
    payload = request.get_json(silent=True) or {}
    if not (payload.get("lyrics") or "").strip():
        return jsonify({"error": "Paste some lyrics to plot."}), 400

    analysis = payload.get("analysis")
    if not isinstance(analysis, dict):
        analysis = None

    try:
        return jsonify(graph.build_graph(compose(payload), analysis))
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"{type(exc).__name__}: {exc}"}), 500


@app.post("/api/analyze-pdf")
def api_analyze_pdf():
    """Runs lyrics_text.detect_emotion() on an uploaded PDF.

    Note: detect_emotion() returns a raw string (the six header lines plus
    prose from its own prompt), not the structured dict analyze() returns.
    The front end renders it as plain text rather than through the same
    circumplex/bars UI used for /api/analyze.
    """
    upload = request.files.get("file")
    if upload is None or not upload.filename:
        return jsonify({"error": "Attach a PDF first."}), 400
    if not upload.filename.lower().endswith(".pdf"):
        return jsonify({"error": "detect_emotion() expects a PDF file."}), 400

    # tempfile.gettempdir() rather than "/tmp", which does not exist on Windows,
    # and secure_filename so an uploaded name cannot walk out of that directory.
    safe_name = secure_filename(upload.filename) or "upload.pdf"
    tmp_path = os.path.join(tempfile.gettempdir(), safe_name)
    upload.save(tmp_path)
    try:
        text = lyrics_senti_analysis.detect_emotion(tmp_path)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"{type(exc).__name__}: {exc}"}), 500
    finally:
        os.remove(tmp_path)

    return jsonify({"raw_text": text})


if __name__ == "__main__":
    app.run(debug=True, port=int(os.getenv("PORT", "5000")))