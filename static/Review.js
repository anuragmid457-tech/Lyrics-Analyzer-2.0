/* ============================================
   LYRIQ REVIEW LAYER

   Mounts itself onto the page that is already there. Nothing in the template
   calls into this file; it wraps renderResult(), adds its own controls to the
   actions row, and owns the drawer. Load it after the page's own script.

   What it adds:
     · a switch choosing learned corrections or the plain model
     · a provenance line under each reading saying which one produced it
     · Edit this reading, which opens the correction drawer
     · a review log of every correction, with retire and restore

   The editor's label fields rest as a single control showing the current
   value. Clicking one opens a panel offering two ways in, each with an
   indicator light: choose from the standard labels, or type your own.
============================================ */

(function () {
    "use strict";

    var API = "/api/review";

    var LABELS = [
        "joy", "love", "longing", "sadness", "grief", "nostalgia",
        "anger", "fear", "anxiety", "peace", "devotion",
        "spiritual_yearning", "hope", "patriotism", "playfulness",
        "loneliness", "acceptance", "wonder", "serenity"
    ];

    var RASAS = [
        "shringara", "karuna", "shanta", "bhakti", "veera",
        "adbhuta", "hasya", "raudra", "bhayanaka", "bibhatsa"
    ];

    var PARJAAY = [
        "Puja", "Prem", "Prakriti", "Swadesh",
        "Anushthanik", "Bichitro", "Nrityanatya"
    ];

    var QUADRANT_LABELS = {
        Q1: "Q1 · happy, excited",
        Q2: "Q2 · tense, agitated",
        Q3: "Q3 · sad, subdued",
        Q4: "Q4 · calm, serene"
    };

    // Same hues the result card uses for its bars, so a label means the same
    // colour wherever it appears.
    var EMOTION_COLOR = {
        love: "#c98293", joy: "#d8ad55", devotion: "#8c81c8",
        longing: "#7e8fd6", sadness: "#668fc0", serenity: "#69a99e",
        anger: "#c86c69", fear: "#c07a3f", peace: "#69a99e",
        grief: "#668fc0", nostalgia: "#8c81c8", anxiety: "#c07a3f",
        hope: "#d8ad55", patriotism: "#c98293", playfulness: "#d8ad55",
        loneliness: "#668fc0", acceptance: "#69a99e", wonder: "#8c81c8",
        spiritual_yearning: "#8c81c8"
    };

    var state = {
        useLearned: true,
        current: null,
        original: null
    };


    /* =========================
       SMALL HELPERS
    ========================= */

    function el(tag, className, text) {
        var node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== undefined) node.textContent = text;
        return node;
    }

    function escapeHTML(value) {
        return String(value === null || value === undefined ? "" : value)
            .replace(/[&<>"]/g, function (character) {
                return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[character];
            });
    }

    function asText(value) {
        if (Array.isArray(value)) return value.join(", ");
        if (value === null || value === undefined) return "";
        return String(value);
    }

    function number(value, fallback) {
        var parsed = Number(value);
        return Number.isFinite(parsed) ? parsed : (fallback || 0);
    }

    function quadrantFrom(valence, arousal) {
        if (valence >= 0) return arousal >= 0 ? "Q1" : "Q4";
        return arousal >= 0 ? "Q2" : "Q3";
    }

    function toast(message) {
        var existing = document.querySelector(".review-toast");
        if (existing) existing.remove();
        var node = el("div", "review-toast", message);
        document.body.appendChild(node);
        setTimeout(function () { node.remove(); }, 2600);
    }

    async function api(path, options) {
        var response = await fetch(API + path, options);
        var data = await response.json().catch(function () { return {}; });
        if (!response.ok) throw new Error(data.error || "The request failed.");
        return data;
    }


    /* =========================
       CONTROLS IN THE ACTIONS ROW
    ========================= */

    function mountControls() {
        var actions = document.querySelector(".actions");
        if (!actions) return;

        var label = el("label", "review-switch");
        var box = document.createElement("input");
        box.type = "checkbox";
        box.id = "use-learned";

        var caption = el("span");
        caption.innerHTML = "Use expert corrections <small id=\"learned-count\"></small>";

        label.appendChild(box);
        label.appendChild(caption);
        actions.appendChild(label);

        var logButton = el("button", "review-log-open", "Review log");
        logButton.type = "button";
        logButton.addEventListener("click", openLog);
        actions.appendChild(logButton);

        box.addEventListener("change", async function () {
            state.useLearned = box.checked;
            label.classList.toggle("on", box.checked);
            try {
                await api("/preferences", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ use_learned: box.checked })
                });
                toast(box.checked
                    ? "Learned corrections on. They will shape the next reading."
                    : "Learned corrections off. Readings come from the model alone.");
            } catch (error) {
                toast(error.message);
            }
        });

        loadPreferences(box, label);
    }

    async function loadPreferences(box, label) {
        try {
            var data = await api("/preferences");
            state.useLearned = data.use_learned;
            box.checked = data.use_learned;
            label.classList.toggle("on", data.use_learned);

            var teaching = (data.stats || {}).teaching || 0;
            var count = document.getElementById("learned-count");
            if (count) {
                count.textContent = teaching
                    ? "· " + teaching + (teaching === 1 ? " edit" : " edits")
                    : "· none yet";
            }
        } catch (error) {
            /* the page still works without the review layer */
        }
    }


    /* =========================
       PROVENANCE + EDIT BUTTON
    ========================= */

    function describe(learning) {
        if (!learning) return null;
        if (learning.source === "correction") {
            return {
                tone: "learned",
                text: "Served from an expert correction of this exact song, saved "
                    + (learning.edited_at || "").slice(0, 10)
                    + (learning.editor ? " by " + learning.editor : "")
                    + ". The model was not asked again."
            };
        }
        if (learning.source === "guided") {
            var count = (learning.matches || []).length;
            return {
                tone: "learned",
                text: "Read with " + count + " past correction"
                    + (count === 1 ? "" : "s") + " on similar songs as guidance."
            };
        }
        return {
            tone: learning.mode === "learned" ? "learned" : "default",
            text: learning.mode === "learned"
                ? "No past correction was close enough to this song, so this is the model's own reading."
                : "Default reading. Learned corrections were switched off."
        };
    }

    function attach(data) {
        if (!data || !data.analysis_id) return;

        state.current = data;
        state.original = data;

        var body = document.getElementById("body");
        if (!body) return;

        var summary = describe(data.learning);
        if (summary) {
            var badge = el("div", "provenance " + summary.tone);
            badge.innerHTML = "<b>" + (summary.tone === "learned" ? "Learned" : "Default")
                + "</b><span>" + escapeHTML(summary.text) + "</span>";

            var matches = (data.learning && data.learning.matches) || [];
            if (matches.length) {
                var why = el("button", "why", "what it leaned on");
                why.type = "button";
                why.addEventListener("click", function () { openMatches(matches); });
                badge.appendChild(why);
            }
            body.appendChild(badge);
        }

        var button = el("button", "secondary edit-output", "Edit this reading");
        button.type = "button";
        button.addEventListener("click", function () { openEditor(data); });
        body.appendChild(button);
    }


    /* =========================
       DRAWER SHELL
    ========================= */

    var drawer = null;

    function closeDrawer() {
        if (!drawer) return;
        drawer.scrim.remove();
        drawer.panel.remove();
        document.removeEventListener("keydown", onEscape);
        drawer = null;
    }

    function onEscape(event) {
        if (event.key === "Escape") closeDrawer();
    }

    function openDrawer(title, blurb) {
        closeDrawer();

        var scrim = el("div", "drawer-scrim");
        scrim.addEventListener("click", closeDrawer);

        var panel = el("aside", "drawer");
        panel.setAttribute("role", "dialog");
        panel.setAttribute("aria-modal", "true");
        panel.setAttribute("aria-label", title);

        var head = el("div", "drawer-head");
        var heading = el("div");
        heading.appendChild(el("div", "result-label", "LYRIQ · Review"));
        heading.appendChild(el("h3", null, title));
        if (blurb) heading.appendChild(el("p", null, blurb));

        var close = el("button", "drawer-close", "✕");
        close.type = "button";
        close.setAttribute("aria-label", "Close");
        close.addEventListener("click", closeDrawer);

        head.appendChild(heading);
        head.appendChild(close);

        var content = el("div", "drawer-body");
        var foot = el("div", "drawer-foot");

        panel.appendChild(head);
        panel.appendChild(content);
        panel.appendChild(foot);

        document.body.appendChild(scrim);
        document.body.appendChild(panel);
        document.addEventListener("keydown", onEscape);

        drawer = { scrim: scrim, panel: panel, body: content, foot: foot };
        return drawer;
    }


    /* =========================
       PICKER
    ========================= */

    function ledRow(text) {
        var row = el("button", "mode-row");
        row.type = "button";
        row.appendChild(el("span", "led"));
        row.appendChild(el("span", null, text));
        return row;
    }

    function blink(row) {
        var led = row.querySelector(".led");
        led.classList.remove("blink");
        void led.offsetWidth;            // restart the animation from the top
        led.classList.add("blink");
    }

    function buildPicker(list, input, options) {
        var node = el("div", "picker");

        // At rest: the current value and nothing else.
        var trigger = el("button", "picker-trigger");
        trigger.type = "button";
        trigger.setAttribute("aria-haspopup", "true");
        trigger.setAttribute("aria-expanded", "false");

        var swatch = el("span", "swatch");
        var valueText = el("span", "picker-value");
        var caret = el("span", "picker-caret", "▾");
        trigger.appendChild(swatch);
        trigger.appendChild(valueText);
        trigger.appendChild(caret);

        // Manual typing swaps the trigger out for this.
        var freeWrap = el("div", "free-wrap");
        freeWrap.hidden = true;
        var back = el("button", "picker-back", "▾");
        back.type = "button";
        back.title = "Back to the list";
        freeWrap.appendChild(input);
        freeWrap.appendChild(back);

        var menu = el("div", "picker-menu");
        menu.hidden = true;

        var listWrap = el("div", "picker-list");
        listWrap.hidden = true;

        var listRow = null;
        var freeRow = null;

        if (!options.strict) {
            var modes = el("div", "picker-modes");
            listRow = ledRow("Choose from the list");
            freeRow = ledRow("Type manually");
            modes.appendChild(listRow);
            modes.appendChild(freeRow);
            menu.appendChild(modes);

            listRow.addEventListener("click", function () {
                blink(listRow);
                listRow.classList.add("on");
                freeRow.classList.remove("on");
                listWrap.hidden = false;
            });

            freeRow.addEventListener("click", function () {
                blink(freeRow);
                freeRow.classList.add("on");
                listRow.classList.remove("on");
                // Let the light register before the panel moves out from under it.
                setTimeout(function () {
                    close();
                    trigger.hidden = true;
                    freeWrap.hidden = false;
                    input.focus();
                    input.select();
                }, 280);
            });
        }

        menu.appendChild(listWrap);

        list.forEach(function (option) {
            if (!option) return;
            var row = el("button", "picker-option");
            row.type = "button";
            row.dataset.value = option;

            var colour = options.swatch ? EMOTION_COLOR[option.toLowerCase()] : null;
            if (colour) {
                var dot = el("span", "swatch");
                dot.style.background = colour;
                row.appendChild(dot);
            }
            row.appendChild(el("span", null, option));

            row.addEventListener("click", function () {
                input.value = option;
                input.dispatchEvent(new Event("change"));
                refresh();
                close();
            });
            listWrap.appendChild(row);
        });

        back.addEventListener("click", function () {
            freeWrap.hidden = true;
            trigger.hidden = false;
            refresh();
            open();
        });

        function refresh() {
            var current = asText(input.value);
            valueText.textContent = current || "Not specified";
            valueText.classList.toggle("empty", !current);

            var colour = options.swatch ? EMOTION_COLOR[current.toLowerCase()] : null;
            swatch.style.background = colour || "transparent";
            swatch.hidden = !colour;

            [].forEach.call(listWrap.children, function (row) {
                row.classList.toggle("on", row.dataset.value === current);
            });
        }

        function open() {
            var room = window.innerHeight - trigger.getBoundingClientRect().bottom;
            node.classList.toggle("up", room < 300);
            listWrap.hidden = !options.strict;   // a closed set has no mode to choose
            menu.hidden = false;
            trigger.setAttribute("aria-expanded", "true");
            document.addEventListener("mousedown", onOutside, true);
            document.addEventListener("keydown", onKey, true);
        }

        function close() {
            menu.hidden = true;
            trigger.setAttribute("aria-expanded", "false");
            document.removeEventListener("mousedown", onOutside, true);
            document.removeEventListener("keydown", onKey, true);
        }

        function onOutside(event) {
            if (!node.contains(event.target)) close();
        }

        function onKey(event) {
            if (event.key !== "Escape") return;
            event.stopPropagation();          // close the menu, not the whole drawer
            close();
            trigger.focus();
        }

        trigger.addEventListener("click", function () {
            if (menu.hidden) { open(); } else { close(); }
        });

        node.appendChild(trigger);
        node.appendChild(freeWrap);
        node.appendChild(menu);

        input._refresh = refresh;
        refresh();

        return { node: node, refresh: refresh };
    }


    /* =========================
       FORM BUILDERS
    ========================= */

    function group(parent, title) {
        parent.appendChild(el("div", "group-title", title));
    }

    function textField(parent, id, label, value, options) {
        options = options || {};

        var wrap = el("div", "field");
        var labelNode = el("label", null, label);
        labelNode.setAttribute("for", id);
        wrap.appendChild(labelNode);

        // Plain text or prose: no list, nothing to choose from.
        if (!options.list) {
            var plain = options.multiline
                ? document.createElement("textarea")
                : document.createElement("input");
            if (options.multiline) { plain.rows = options.rows || 4; }
            else { plain.type = "text"; }

            plain.id = id;
            plain.value = asText(value);
            if (options.placeholder) plain.placeholder = options.placeholder;
            wrap.appendChild(plain);

            if (options.hint) wrap.appendChild(el("span", "was", options.hint));
            if (options.was !== undefined && asText(options.was) !== "") {
                var wasNote = el("span", "was");
                wasNote.innerHTML = "model said <b>" + escapeHTML(asText(options.was)) + "</b>";
                wrap.appendChild(wasNote);
            }
            parent.appendChild(wrap);
            return plain;
        }

        // The input is the single source of truth in both modes; while the
        // list is showing it sits hidden and the picker writes into it.
        var input = document.createElement("input");
        input.type = "text";
        input.id = id;
        input.className = "free-input";
        input.value = asText(value);
        input.placeholder = options.placeholder || "Type anything, e.g. playfulness / love";

        var picker = buildPicker(options.list, input, options);
        wrap.appendChild(picker.node);

        if (options.hint) wrap.appendChild(el("span", "was", options.hint));
        if (options.was !== undefined && asText(options.was) !== "") {
            var was = el("span", "was");
            was.innerHTML = "model said <b>" + escapeHTML(asText(options.was)) + "</b>";
            wrap.appendChild(was);
        }

        parent.appendChild(wrap);
        return input;
    }

    function sliderField(parent, id, label, value, low, high, was) {
        var wrap = el("div", "field");
        wrap.appendChild(el("label", null, label)).setAttribute("for", id);

        var row = el("div", "slider-row");
        var input = document.createElement("input");
        input.type = "range";
        input.id = id;
        input.min = low;
        input.max = high;
        input.step = 0.01;
        input.value = number(value);

        var readout = document.createElement("output");
        readout.textContent = number(value).toFixed(2);

        input.addEventListener("input", function () {
            readout.textContent = number(input.value).toFixed(2);
            syncQuadrant();
        });

        row.appendChild(input);
        row.appendChild(readout);
        wrap.appendChild(row);

        if (was !== undefined && was !== null) {
            var note = el("span", "was");
            note.innerHTML = "model said <b>" + number(was).toFixed(2) + "</b>";
            wrap.appendChild(note);
        }
        parent.appendChild(wrap);
        return input;
    }

    function syncQuadrant() {
        var valence = document.getElementById("edit-valence");
        var arousal = document.getElementById("edit-arousal");
        var quadrant = document.getElementById("edit-quadrant");
        if (!valence || !arousal || !quadrant) return;
        if (quadrant.dataset.touched === "1") return;
        quadrant.value = quadrantFrom(number(valence.value), number(arousal.value));
        if (quadrant._refresh) quadrant._refresh();
    }


    /* =========================
       EDITOR
    ========================= */

    function openEditor(data) {
        var shell = openDrawer(
            "Correct this reading",
            "Change what the model got wrong, then say why. The reasoning is what "
            + "teaches the next reading; a changed number on its own teaches very little."
        );

        var body = shell.body;

        var errorSlot = el("div");
        body.appendChild(errorSlot);

        group(body, "Verdict");

        var primary = textField(body, "edit-primary", "Primary emotion",
            data.primary_emotion, { list: LABELS.slice(), swatch: true });

        var secondary = textField(body, "edit-secondary",
            "Secondary emotions, comma separated", data.secondary_emotions,
            { placeholder: "longing, devotion" });

        var mixedWrap = el("div", "field");
        var mixedLabel = el("label", "check-row");
        var mixed = document.createElement("input");
        mixed.type = "checkbox";
        mixed.id = "edit-mixed";
        mixed.checked = Boolean(data.mixed_emotion);
        mixedLabel.appendChild(mixed);
        mixedLabel.appendChild(el("span", null, "Mixed expression, sorrow and serenity together"));
        mixedWrap.appendChild(mixedLabel);
        body.appendChild(mixedWrap);

        group(body, "Circumplex");

        var valence = sliderField(body, "edit-valence", "Valence",
            data.valence, -1, 1, data.valence);
        var arousal = sliderField(body, "edit-arousal", "Arousal",
            data.arousal, -1, 1, data.arousal);

        // Closed set: Q1 to Q4 are defined by the signs of valence and arousal,
        // so a typed value here could only contradict the two sliders above.
        var quadrant = textField(body, "edit-quadrant", "Quadrant", data.quadrant, {
            list: ["Q1", "Q2", "Q3", "Q4"],
            strict: true,
            hint: "follows valence and arousal until you choose one yourself"
        });
        quadrant.addEventListener("change", function () {
            quadrant.dataset.touched = "1";
        });

        var confidence = sliderField(body, "edit-confidence", "Confidence",
            data.confidence, 0, 1, data.confidence);

        group(body, "Cultural reading");

        var rasa = textField(body, "edit-rasa", "Rasa", data.rasa,
            { list: RASAS.slice(), placeholder: "Type a rasa, or a compound one" });

        var parjaay = textField(body, "edit-parjaay", "Parjaay", data.parjaay,
            { list: PARJAAY.slice(), placeholder: "Type a parjaay" });

        var pair = el("div", "field-pair");
        body.appendChild(pair);
        var tradition = textField(pair, "edit-tradition", "Tradition", data.tradition, {});
        var language = textField(pair, "edit-language", "Language", data.language, {});

        group(body, "Prose");

        var summary = textField(body, "edit-summary", "The reading", data.summary,
            { multiline: true, rows: 6 });

        var therapy = textField(body, "edit-therapy", "Music therapy use",
            data.music_therapy_context || data.music_therapy, {});

        var tags = textField(body, "edit-tags", "Recommendation tags, comma separated",
            data.recommendation_tags, { placeholder: "late-night, devotional" });

        group(body, "Who and why");

        var editor = textField(body, "edit-editor", "Reviewer", "",
            { placeholder: "Your name, so the log shows who decided this" });

        var note = textField(body, "edit-note", "Why the model was wrong", "", {
            multiline: true,
            rows: 4,
            placeholder: "e.g. biraha in Baul is separation from the divine, "
                + "not plain sadness, so valence should not sit this low."
        });

        var save = el("button", "primary", "Save correction");
        save.type = "button";

        var cancel = el("button", "secondary", "Cancel");
        cancel.type = "button";
        cancel.addEventListener("click", closeDrawer);

        shell.foot.appendChild(save);
        shell.foot.appendChild(cancel);

        save.addEventListener("click", async function () {
            errorSlot.innerHTML = "";
            save.disabled = true;
            save.textContent = "Saving";

            var corrected = {
                primary_emotion: primary.value.trim(),
                secondary_emotions: secondary.value,
                mixed_emotion: mixed.checked,
                valence: number(valence.value),
                arousal: number(arousal.value),
                quadrant: quadrant.value,
                confidence: number(confidence.value),
                rasa: rasa.value.trim() || null,
                parjaay: parjaay.value.trim() || null,
                tradition: tradition.value.trim() || null,
                language: language.value.trim() || null,
                summary: summary.value.trim(),
                music_therapy: therapy.value.trim(),
                music_therapy_context: therapy.value.trim(),
                recommendation_tags: tags.value
            };

            try {
                var result = await api("/correction", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        analysis_id: data.analysis_id,
                        corrected: corrected,
                        editor: editor.value,
                        note: note.value
                    })
                });

                closeDrawer();
                repaint(result.corrected, data, result);
                toast(result.embedded
                    ? "Correction saved. It will guide similar songs from now on."
                    : "Correction saved. Embeddings were unavailable, so it will "
                    + "apply to this exact song only.");
            } catch (error) {
                errorSlot.appendChild(el("div", "drawer-error", error.message));
                save.disabled = false;
                save.textContent = "Save correction";
            }
        });
    }

    function repaint(corrected, previous, result) {
        var merged = Object.assign({}, previous, corrected);
        merged.analysis_id = previous.analysis_id;
        merged.quadrant_label = QUADRANT_LABELS[merged.quadrant] || merged.quadrant;
        merged.learning = {
            mode: "learned",
            source: "correction",
            correction_id: result.correction_id,
            edited_at: new Date().toISOString(),
            editor: null,
            matches: []
        };
        if (typeof window.renderResult === "function") {
            window.renderResult(merged);
        }
    }


    /* =========================
       WHAT IT LEANED ON
    ========================= */

    function openMatches(matches) {
        var shell = openDrawer(
            "What shaped this reading",
            "Past corrections the analyser was shown before reading this song."
        );

        matches.forEach(function (match) {
            var row = el("div", "log-row");

            var top = el("div", "log-top");
            top.appendChild(el("p", "log-excerpt", match.excerpt));
            top.appendChild(el("span", "log-meta", "similarity " + match.similarity));
            row.appendChild(top);

            var fields = el("div", "log-fields");
            (match.fields || []).forEach(function (field) {
                fields.appendChild(el("span", "pill", field.replace(/_/g, " ")));
            });
            row.appendChild(fields);

            if (match.note) row.appendChild(el("p", "log-note", match.note));
            shell.body.appendChild(row);
        });

        var done = el("button", "secondary", "Close");
        done.type = "button";
        done.addEventListener("click", closeDrawer);
        shell.foot.appendChild(done);
    }


    /* =========================
       REVIEW LOG
    ========================= */

    async function openLog() {
        var shell = openDrawer(
            "Review log",
            "Every correction on file. Retiring one keeps the record but stops it "
            + "teaching from the next reading onward."
        );

        shell.body.appendChild(el("p", "log-empty", "Loading…"));

        try {
            var data = await api("/corrections?limit=100");
            shell.body.innerHTML = "";

            var stats = data.stats || {};
            var summary = el("p", "drawer-note");
            summary.textContent = stats.corrections
                ? stats.corrections + " correction"
                    + (stats.corrections === 1 ? "" : "s") + " on file, "
                    + stats.teaching + " still teaching, across "
                    + stats.analyses + " readings."
                : "No corrections yet. Read a song, then edit what the model got wrong.";
            shell.body.appendChild(summary);

            (data.corrections || []).forEach(function (correction) {
                shell.body.appendChild(logRow(correction));
            });
        } catch (error) {
            shell.body.innerHTML = "";
            shell.body.appendChild(el("div", "drawer-error", error.message));
        }

        var done = el("button", "secondary", "Close");
        done.type = "button";
        done.addEventListener("click", closeDrawer);
        shell.foot.appendChild(done);
    }

    function logRow(correction) {
        var row = el("div", "log-row" + (correction.active ? "" : " retired"));

        var top = el("div", "log-top");
        top.appendChild(el("p", "log-excerpt", correction.excerpt));
        top.appendChild(el("span", "log-meta",
            (correction.created_at || "").slice(0, 10)
            + (correction.editor ? " · " + correction.editor : "")));
        row.appendChild(top);

        var fields = el("div", "log-fields");
        Object.keys(correction.changed || {}).forEach(function (field) {
            var move = correction.changed[field];
            fields.appendChild(el("span", "pill",
                field.replace(/_/g, " ") + " → " + (move.to || "cleared").slice(0, 28)));
        });
        row.appendChild(fields);

        if (correction.note) row.appendChild(el("p", "log-note", correction.note));

        var actions = el("div", "log-actions");
        var toggle = el("button", "log-toggle",
            correction.active ? "Retire this correction" : "Put it back to work");
        toggle.type = "button";
        toggle.addEventListener("click", async function () {
            try {
                var result = await api("/correction/" + correction.id + "/retire", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ restore: !correction.active })
                });
                correction.active = result.active ? 1 : 0;
                row.classList.toggle("retired", !result.active);
                toggle.textContent = result.active
                    ? "Retire this correction"
                    : "Put it back to work";
                toast(result.active ? "Teaching again." : "Retired. It stops teaching now.");
            } catch (error) {
                toast(error.message);
            }
        });
        actions.appendChild(toggle);
        row.appendChild(actions);

        return row;
    }


    /* =========================
       MOUNT
    ========================= */

    function start() {
        mountControls();

        // renderResult is a top-level function declaration in the page's own
        // script, so it lives on the global object and can be wrapped here.
        var original = window.renderResult;
        if (typeof original === "function") {
            window.renderResult = function (data) {
                original(data);
                attach(data);
            };
        }
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", start);
    } else {
        start();
    }
})();