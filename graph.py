"""
graph.py - the model draws the song, not just labels it.

analyze() gives one verdict for a whole song. That flattens a text like a Baul
song or a Tagore Prem song whose refrain reverses its verses. So this module
asks the model to walk the lyrics in order, split them into the sections the
song itself suggests, and score each one on the circumplex. The result is an
emotion arc rather than a single dot.

Everything here returns plain JSON-serialisable dicts shaped as Plotly figures,
so the browser only has to hand them to Plotly.react and nothing about the
model leaks into the front end. The palette matches the LYRIQ page tokens.

    build_graph(text, analysis=None) -> dict
        {
          "figures": {"arc": {...}, "circumplex": {...}, "profile": {...}},
          "segments": [...],
          "distribution": {...},
          "caption": "...",
          "config":  {...}
        }
"""

import json

from dotenv import load_dotenv
from langchain.chat_models import init_chat_model
from langchain_core.messages import HumanMessage, SystemMessage

load_dotenv()

EMOTIONS = [
    "joy", "love", "serenity", "devotion",
    "longing", "sadness", "fear", "anger",
]

SYSTEM_PROMPT = """You segment a song and score each segment for plotting on Russell's \
circumplex. You handle Bengali and South Asian repertoire as first class cases: \
Rabindrasangeet, Baul sangeet, Lalan geeti, Sufi and qawwali, Shyama sangeet, Nazrul geeti, \
kirtan and bhajan, alongside general popular song. Lyrics may be in Bengali, Devanagari or \
Perso Arabic script, in roman transliteration, or code mixed with English, and may arrive with \
notation, raga, taal, laya or parjaay metadata appended after a line of three dashes.

Split the lyrics into the sections the song itself suggests: sthayi, antara, sanchari, abhog, \
or verse, refrain, bridge, or simply consecutive stanzas. Between three and eight segments. \
Keep them in the order they appear in the text. Never reorder, never merge distant parts, and \
never invent a section that is not in the supplied text.

For every segment give:
  label     a short name for that section, two or three words, taken from its function or its
            opening image, not a number on its own
  snippet   at most six words quoted from that segment, enough to locate it
  valence   minus one to one, two decimals, minus one maximally unpleasant
  arousal   minus one to one, two decimals, minus one maximally calm
  emotion   one label from: joy, love, serenity, devotion, longing, sadness, fear, anger
  note      one short sentence, plain text, naming the word or image that set those numbers

Then give distribution, the emotional weight of the song as a whole across all eight labels, \
each between zero and one with two decimals, summing to 1.00.

Then give caption, two or three plain sentences describing the shape of the arc: where it \
moves and what moves it. Name any point where the surface words and the underlying meaning \
diverge, which is common where separation from the divine is written in the vocabulary of \
loss. Read metaphor as metaphor and do not score images literally. Where notation, raga or \
taal is supplied, let it inform arousal but let the words lead, and say so if they conflict.

You are scoring perceived emotion, the emotion the song expresses, not the emotion a listener \
would feel. Judge only from the supplied text. Do not recall the rest of a song you think you \
recognise. If the input is a fragment, score the fragment and say so in the caption.

Write plain text in label, snippet, note and caption. No markdown, no asterisks, no bullets.

Return ONLY a raw JSON object, no markdown fences and no commentary, in exactly this shape:

{
  "segments": [
    {"label": "", "snippet": "", "valence": 0.0, "arousal": 0.0, "emotion": "", "note": ""}
  ],
  "distribution": {"joy": 0.0, "love": 0.0, "serenity": 0.0, "devotion": 0.0,
                   "longing": 0.0, "sadness": 0.0, "fear": 0.0, "anger": 0.0},
  "caption": ""
}
"""

model = init_chat_model(
    "gemini-3.1-flash-lite-preview",
    model_provider="google_genai",
    temperature=0.2,
)

# --- palette -------------------------------------------------------------
# Same tokens the page uses, so the charts read as part of LYRIQ rather than
# as an embedded plotting library. Valence is gold, arousal teal, and the two
# lines also differ in dash and marker shape so hue is never the only cue.

TEXT = "#eeeae2"
SOFT = "#c6cad1"
MUTED = "#8992a1"
GRID = "rgba(137,146,161,0.14)"
AXIS = "rgba(137,146,161,0.32)"
SURFACE = "#151a22"
LINE = "#272e39"

GOLD = "#d8ad55"
TEAL = "#69a99e"
VIOLET = "#8c81c8"

SANS = '"DM Sans","Noto Sans Bengali",system-ui,sans-serif'
SERIF = 'Newsreader,Georgia,serif'

EMOTION_COLOR = {
    "love": "#c98293",
    "joy": "#d8ad55",
    "devotion": "#8c81c8",
    "longing": "#7e8fd6",
    "sadness": "#668fc0",
    "serenity": "#69a99e",
    "anger": "#c86c69",
    "fear": "#c07a3f",
}

QUADRANT_TINT = {
    "Q1": "rgba(216,173,85,.055)",
    "Q2": "rgba(200,108,105,.055)",
    "Q3": "rgba(102,143,192,.055)",
    "Q4": "rgba(105,169,158,.055)",
}
QUADRANT_NAME = {
    "Q1": "bright, rising",
    "Q2": "tense, agitated",
    "Q3": "subdued, heavy",
    "Q4": "calm, settled",
}


# --- parsing -------------------------------------------------------------

def _text_of(raw):
    if isinstance(raw, list):
        return "".join(
            block.get("text", "") if isinstance(block, dict) else str(block)
            for block in raw
        )
    return raw or ""


def _loads(raw):
    """Same fence-stripping analyze() does, kept local so the two stay independent."""
    cleaned = _text_of(raw).strip()
    if cleaned.startswith("```"):
        parts = cleaned.split("```")
        if len(parts) > 1:
            cleaned = parts[1]
        if cleaned.lstrip().lower().startswith("json"):
            cleaned = cleaned.lstrip()[4:]
    cleaned = cleaned.strip("` \n")
    return json.loads(cleaned)


def _num(value, low=-1.0, high=1.0, default=0.0):
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    if out != out:  # NaN
        return default
    return max(low, min(high, out))


def _quadrant(valence, arousal):
    if valence >= 0:
        return "Q1" if arousal >= 0 else "Q4"
    return "Q2" if arousal >= 0 else "Q3"


def _clean_segments(raw_segments):
    out = []
    for i, seg in enumerate(raw_segments or []):
        if not isinstance(seg, dict):
            continue
        valence = _num(seg.get("valence"))
        arousal = _num(seg.get("arousal"))
        emotion = str(seg.get("emotion") or "").strip().lower().replace(" ", "_")
        out.append({
            "label": str(seg.get("label") or "").strip() or f"Part {i + 1}",
            "snippet": str(seg.get("snippet") or "").strip(),
            "valence": round(valence, 2),
            "arousal": round(arousal, 2),
            "emotion": emotion if emotion in EMOTION_COLOR else "",
            "note": str(seg.get("note") or "").strip(),
            "quadrant": _quadrant(valence, arousal),
        })
    return out


def _clean_distribution(raw_distribution):
    values = {key: _num((raw_distribution or {}).get(key), 0.0, 1.0) for key in EMOTIONS}
    total = sum(values.values())
    if total <= 0:
        return {key: round(1.0 / len(EMOTIONS), 2) for key in EMOTIONS}
    return {key: round(value / total, 3) for key, value in values.items()}


def _from_analysis(analysis):
    """Last resort: one point and a single-label profile, built from analyze()'s verdict."""
    analysis = analysis if isinstance(analysis, dict) else {}
    valence = _num(analysis.get("valence"))
    arousal = _num(analysis.get("arousal"))
    primary = str(analysis.get("primary_emotion") or "").strip().lower().replace(" ", "_")
    distribution = {key: 0.0 for key in EMOTIONS}
    if primary in distribution:
        distribution[primary] = 1.0
    return {
        "segments": [{
            "label": "Whole song",
            "snippet": "",
            "valence": round(valence, 2),
            "arousal": round(arousal, 2),
            "emotion": primary if primary in EMOTION_COLOR else "",
            "note": str(analysis.get("summary") or "").strip(),
            "quadrant": _quadrant(valence, arousal),
        }],
        "distribution": distribution,
        "caption": "The section by section reading was unavailable, so this shows the single "
                   "whole song verdict only.",
    }


def _colors_for(segments):
    return [EMOTION_COLOR.get(s["emotion"], VIOLET) for s in segments]


# --- figures -------------------------------------------------------------

def _base_layout(title, **extra):
    layout = {
        "title": {
            "text": title,
            "font": {"family": SERIF, "size": 21, "color": TEXT},
            "x": 0, "xanchor": "left", "y": 0.97,
        },
        "margin": {"l": 58, "r": 26, "t": 56, "b": 52},
        "paper_bgcolor": "rgba(0,0,0,0)",
        "plot_bgcolor": "rgba(0,0,0,0)",
        "font": {"family": SANS, "color": MUTED, "size": 12},
        "hoverlabel": {
            "align": "left",
            "bgcolor": SURFACE,
            "bordercolor": LINE,
            "font": {"family": SANS, "color": TEXT, "size": 12},
        },
        "dragmode": "pan",
    }
    layout.update(extra)
    return layout


def _arc_figure(segments):
    labels = [s["label"] for s in segments]
    custom = [[s["snippet"] or "—", s["emotion"] or "unnamed", s["note"]] for s in segments]
    hover = (
        "<b>%{x}</b><br>%{fullData.name} %{y:.2f}"
        "<br>reads as %{customdata[1]}"
        "<br><i>%{customdata[0]}</i>"
        "<br>%{customdata[2]}<extra></extra>"
    )
    return {
        "data": [
            {
                "type": "scatter", "name": "Valence", "mode": "lines+markers",
                "x": labels, "y": [s["valence"] for s in segments],
                "line": {"color": GOLD, "width": 2, "shape": "spline", "smoothing": 0.7},
                "marker": {"size": 10, "symbol": "circle", "color": GOLD},
                "customdata": custom, "hovertemplate": hover,
            },
            {
                "type": "scatter", "name": "Arousal", "mode": "lines+markers",
                "x": labels, "y": [s["arousal"] for s in segments],
                "line": {"color": TEAL, "width": 2, "shape": "spline",
                         "smoothing": 0.7, "dash": "dot"},
                "marker": {"size": 10, "symbol": "diamond", "color": TEAL},
                "customdata": custom, "hovertemplate": hover,
            },
        ],
        "layout": _base_layout(
            "How the song moves",
            xaxis={"showgrid": False, "linecolor": LINE, "tickfont": {"size": 11, "color": SOFT}},
            yaxis={"range": [-1.08, 1.08], "zeroline": True, "zerolinecolor": AXIS,
                   "zerolinewidth": 1, "gridcolor": GRID, "dtick": 0.5, "tickformat": ".1f"},
            hovermode="closest",
            legend={"orientation": "h", "y": -0.2, "x": 0,
                    "font": {"color": SOFT, "size": 12}},
        ),
    }


def _circumplex_figure(segments, analysis=None):
    shapes, annotations = [], []
    for code, (x0, x1, y0, y1) in {
        "Q1": (0, 1.1, 0, 1.1), "Q2": (-1.1, 0, 0, 1.1),
        "Q3": (-1.1, 0, -1.1, 0), "Q4": (0, 1.1, -1.1, 0),
    }.items():
        shapes.append({
            "type": "rect", "x0": x0, "x1": x1, "y0": y0, "y1": y1,
            "fillcolor": QUADRANT_TINT[code], "line": {"width": 0}, "layer": "below",
        })
        annotations.append({
            "x": (x0 + x1) / 2, "y": y1 - 0.07 if y1 > 0 else y0 + 0.07,
            "text": QUADRANT_NAME[code], "showarrow": False,
            "font": {"family": SANS, "size": 10, "color": MUTED},
        })

    data = [{
        "type": "scatter", "name": "Path", "mode": "lines+markers+text",
        "x": [s["valence"] for s in segments], "y": [s["arousal"] for s in segments],
        "text": [f"{i + 1} · {s['label']}" for i, s in enumerate(segments)],
        "textposition": "top center",
        "textfont": {"family": SANS, "size": 10, "color": SOFT},
        "line": {"color": "rgba(140,129,200,.55)", "width": 1.4,
                 "shape": "spline", "smoothing": 0.5},
        "marker": {"size": 14, "color": _colors_for(segments),
                   "line": {"color": "#080a0f", "width": 2}},
        "customdata": [[s["snippet"] or "—", s["emotion"] or "unnamed", s["note"]]
                       for s in segments],
        "hovertemplate": ("<b>%{text}</b><br>valence %{x:.2f}, arousal %{y:.2f}"
                          "<br>reads as %{customdata[1]}"
                          "<br><i>%{customdata[0]}</i>"
                          "<br>%{customdata[2]}<extra></extra>"),
    }]

    if isinstance(analysis, dict) and analysis.get("valence") is not None:
        data.append({
            "type": "scatter", "name": "Whole song", "mode": "markers",
            "x": [_num(analysis.get("valence"))], "y": [_num(analysis.get("arousal"))],
            "marker": {"size": 17, "symbol": "star", "color": GOLD,
                       "line": {"color": "#080a0f", "width": 1.5}},
            "hovertemplate": ("<b>Whole song verdict</b>"
                              "<br>valence %{x:.2f}, arousal %{y:.2f}<extra></extra>"),
        })

    return {
        "data": data,
        "layout": _base_layout(
            "The path it takes",
            xaxis={"title": {"text": "valence", "font": {"size": 11}},
                   "range": [-1.12, 1.12], "zeroline": True, "zerolinecolor": AXIS,
                   "gridcolor": "rgba(0,0,0,0)", "dtick": 0.5,
                   "tickfont": {"size": 10}},
            yaxis={"title": {"text": "arousal", "font": {"size": 11}},
                   "range": [-1.12, 1.12], "zeroline": True, "zerolinecolor": AXIS,
                   "gridcolor": "rgba(0,0,0,0)", "dtick": 0.5,
                   "tickfont": {"size": 10}, "scaleanchor": "x", "scaleratio": 1},
            shapes=shapes, annotations=annotations,
            showlegend=False, hovermode="closest",
        ),
    }


def _profile_figure(distribution):
    ordered = sorted(distribution.items(), key=lambda kv: kv[1])
    return {
        "data": [{
            "type": "bar", "orientation": "h",
            "x": [round(v, 3) for _, v in ordered],
            "y": [k for k, _ in ordered],
            "marker": {
                "color": [EMOTION_COLOR.get(k, VIOLET) for k, _ in ordered],
                "opacity": 0.88,
                "line": {"width": 0},
            },
            "hovertemplate": "%{y} · %{x:.0%} of the weight<extra></extra>",
        }],
        "layout": _base_layout(
            "Where the weight sits",
            xaxis={"range": [0, max(list(distribution.values()) + [0.1]) * 1.15],
                   "tickformat": ".0%", "gridcolor": GRID, "zeroline": False,
                   "tickfont": {"size": 10}},
            yaxis={"showgrid": False, "tickfont": {"size": 12, "color": SOFT}},
            margin={"l": 92, "r": 26, "t": 56, "b": 44},
            showlegend=False, bargap=0.42,
        ),
    }


# --- entry point ---------------------------------------------------------

def build_graph(text: str, analysis: dict | None = None) -> dict:
    """Score a song section by section and return three ready-to-draw figures."""
    text = (text or "").strip()
    if not text:
        raise ValueError("No lyrics to plot.")

    raw = model.invoke([
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=f"Segment and score this song:\n\n{text}"),
    ]).content

    try:
        payload = _loads(raw)
        segments = _clean_segments(payload.get("segments"))
        if not segments:
            raise ValueError("no usable segments")
        distribution = _clean_distribution(payload.get("distribution"))
        caption = str(payload.get("caption") or "").strip()
    except (json.JSONDecodeError, ValueError, AttributeError):
        fallback = _from_analysis(analysis)
        segments = fallback["segments"]
        distribution = fallback["distribution"]
        caption = fallback["caption"]

    return {
        "segments": segments,
        "distribution": distribution,
        "caption": caption,
        "figures": {
            "arc": _arc_figure(segments),
            "circumplex": _circumplex_figure(segments, analysis),
            "profile": _profile_figure(distribution),
        },
        "config": {
            "responsive": True,
            "displaylogo": False,
            "scrollZoom": False,
            "modeBarButtonsToRemove": [
                "select2d", "lasso2d", "autoScale2d", "toggleSpikelines",
                "hoverClosestCartesian", "hoverCompareCartesian",
            ],
        },
    }