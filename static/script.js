
// ── Sidebar toggle ────────────────────────────────────────────────────────────

(function () {
    const toggle  = document.getElementById('sidebar-toggle');
    const sidebar = document.querySelector('.sidebar');
    const main    = document.querySelector('.main');

    toggle.addEventListener('click', () => {
        const isCollapsed = sidebar.classList.toggle('collapsed');
        main.style.marginLeft = isCollapsed ? '0' : '280px';
        toggle.style.left     = isCollapsed ? '0px' : '266px';
        toggle.querySelector('svg polyline').setAttribute(
            'points',
            isCollapsed ? '9 18 15 12 9 6' : '15 18 9 12 15 6'
        );
    });
}());

// ── Tab switching ─────────────────────────────────────────────────────────────

function switchTab(tab) {
    const isCourse = tab.startsWith("course-");
    document.getElementById("panel-notes").classList.toggle("hidden",   tab !== "notes");
    document.getElementById("panel-socratic").classList.toggle("hidden", tab !== "socratic");
    document.getElementById("panel-quiz").classList.toggle("hidden",     tab !== "quiz");
    document.getElementById("panel-progress").classList.toggle("hidden", tab !== "progress");
    document.getElementById("panel-course").classList.toggle("hidden",   !isCourse);

    document.querySelectorAll(".nav-item").forEach((el) => {
        el.classList.toggle("active", el.dataset.tab === tab);
    });

    if (tab === "progress") renderProgress();
    if (isCourse) renderCoursePage(tab.slice("course-".length));
}

document.querySelectorAll(".nav-item[data-tab]").forEach((btn) => {
    btn.addEventListener("click", () => switchTab(btn.dataset.tab));
});

// ── Subjects: sidebar list + creation modal ────────────────────────────────────

// Renders the sidebar's "Subjects" list from real data (no hardcoded courses).
function renderSidebarSubjects() {
    const container = document.getElementById("sidebar-subjects");
    const subjects = getSubjects();

    if (!subjects.length) {
        container.innerHTML = `<p class="sidebar-empty-hint">Upload notes to create your first subject.</p>`;
        return;
    }

    container.innerHTML = subjects.map(s => `
        <button class="nav-item" data-tab="course-${s.id}" data-subject-id="${s.id}">
            <i class="ti ti-book-2"></i> ${s.name}
        </button>`).join("");

    container.querySelectorAll("[data-subject-id]").forEach(btn => {
        btn.addEventListener("click", () => {
            setActiveSubject(btn.dataset.subjectId);
            switchTab(btn.dataset.tab);
        });
    });
}
renderSidebarSubjects();

// Loose "is this the same subject" check — case/punctuation-insensitive
// equality, substring containment (e.g. "Econ" vs "Econ 101"), or majority
// word overlap. Deliberately conservative-leaning: false positives just
// prompt an extra confirm click, false negatives silently duplicate subjects.
function normalizeSubjectName(name) {
    return name.toLowerCase().trim().replace(/[^a-z0-9]+/g, " ").replace(/\s+/g, " ").trim();
}

function findCloseSubjectMatch(name) {
    const norm = normalizeSubjectName(name);
    if (!norm) return null;

    for (const s of getSubjects()) {
        const sNorm = normalizeSubjectName(s.name);
        if (!sNorm) continue;
        if (sNorm === norm || sNorm.includes(norm) || norm.includes(sNorm)) return s;

        const wordsA = new Set(norm.split(" "));
        const wordsB = new Set(sNorm.split(" "));
        const shared = [...wordsA].filter(w => wordsB.has(w)).length;
        const union  = new Set([...wordsA, ...wordsB]).size;
        if (union > 0 && shared / union >= 0.6) return s;
    }
    return null;
}

let _pendingSubjectMatchId = null;

function updateSubjectMatchNotice() {
    const input     = document.getElementById("subject-name-input");
    const notice    = document.getElementById("subject-match-notice");
    const confirmBtn = document.getElementById("subject-confirm-btn");
    const match = findCloseSubjectMatch(input.value);

    _pendingSubjectMatchId = match ? match.id : null;

    if (match) {
        document.getElementById("subject-match-name").textContent   = match.name;
        document.getElementById("subject-match-name-2").textContent = match.name;
        notice.classList.remove("hidden");
        confirmBtn.classList.add("hidden");
    } else {
        notice.classList.add("hidden");
        confirmBtn.classList.remove("hidden");
    }
}

function openSubjectModal(suggestedName) {
    const modal    = document.getElementById("subject-confirm-modal");
    const input    = document.getElementById("subject-name-input");
    const subtitle = document.getElementById("subject-modal-subtitle");

    input.value = suggestedName || "";
    subtitle.textContent = suggestedName
        ? "Suggested from your notes — edit if you'd like, then confirm."
        : "Couldn't suggest a name automatically — enter one yourself.";

    updateSubjectMatchNotice();
    modal.classList.remove("hidden");
    input.focus();
}

function closeSubjectModal() {
    document.getElementById("subject-confirm-modal").classList.add("hidden");
}

function finishSubjectSelection() {
    const activeSubject = getSubjects().find(s => s.id === getActiveSubjectId());
    if (activeSubject) currentNotesSubjectName = activeSubject.name;

    closeSubjectModal();
    renderSidebarSubjects();
    if (!document.getElementById("panel-progress").classList.contains("hidden")) renderProgress();
}

document.getElementById("subject-name-input").addEventListener("input", updateSubjectMatchNotice);

document.getElementById("subject-confirm-btn").addEventListener("click", () => {
    const name = document.getElementById("subject-name-input").value.trim();
    if (!name) return;
    createSubject(name);
    finishSubjectSelection();
});

document.getElementById("subject-use-existing-btn").addEventListener("click", () => {
    if (_pendingSubjectMatchId) setActiveSubject(_pendingSubjectMatchId);
    finishSubjectSelection();
});

document.getElementById("subject-make-new-btn").addEventListener("click", () => {
    const name = document.getElementById("subject-name-input").value.trim();
    if (!name) return;
    createSubject(name);
    finishSubjectSelection();
});

document.getElementById("subject-skip-btn").addEventListener("click", () => {
    closeSubjectModal();
});

// ── Drop zones ────────────────────────────────────────────────────────────────

function initDropZone(zoneId, inputId) {
    const zone  = document.getElementById(zoneId);
    const input = document.getElementById(inputId);

    function showConfirmed(file) {
        const sizeMB = (file.size / 1_048_576).toFixed(1);
        zone.classList.remove("dragover");
        zone.classList.add("file-confirmed");
        // Hide the full-overlay input so the ✕ button is clickable
        input.style.display = "none";
        // Hide the upload icon so only the filename row shows, centered
        const icon = zone.querySelector("svg");
        if (icon) icon.style.display = "none";

        const wrap = document.createElement("span");
        wrap.className = "dz-confirmed";
        wrap.innerHTML =
            `<span class="dz-check">✓</span>` +
            `<span class="dz-name">${file.name}</span>` +
            `<span class="dz-size">${sizeMB} MB</span>` +
            `<button type="button" class="dz-clear" title="Clear">✕</button>`;

        zone.querySelector(".drop-zone-label").replaceWith(wrap);

        zone.querySelector(".dz-clear").addEventListener("click", (e) => {
            e.stopPropagation();
            clearZone();
        });
    }

    function clearZone() {
        zone.classList.remove("file-confirmed");
        input.value = "";
        input.style.display = "";
        // Restore the upload icon
        const icon = zone.querySelector("svg");
        if (icon) icon.style.display = "";

        const confirmed = zone.querySelector(".dz-confirmed");
        if (confirmed) {
            const label = document.createElement("span");
            label.className = "drop-zone-label";
            label.textContent = "Drag & drop a file here, or click to browse";
            confirmed.replaceWith(label);
        }
    }

    zone.addEventListener("dragover", (e) => {
        e.preventDefault();
        zone.classList.add("dragover");
    });

    zone.addEventListener("dragleave", () => zone.classList.remove("dragover"));

    zone.addEventListener("drop", (e) => {
        e.preventDefault();
        zone.classList.remove("dragover");
        const file = e.dataTransfer.files[0];
        if (!file) return;
        const dt = new DataTransfer();
        dt.items.add(file);
        input.files = dt.files;
        showConfirmed(file);
    });

    input.addEventListener("change", () => {
        if (input.files[0]) showConfirmed(input.files[0]);
    });
}

initDropZone("drop-zone",         "file-input");
initDropZone("socratic-drop-zone","socratic-file-input");
initDropZone("quiz-drop-zone",    "quiz-file-input");

// ── Generate Notes ────────────────────────────────────────────────────────────

let socraticGoals = [];
let goalIndex     = 0;
let pairIndex     = 0;
// Best-guess title for the downloaded PDF — set from the model's suggestion
// right away, then refined to the actually-confirmed subject name once the
// subject modal resolves (see finishSubjectSelection).
let currentNotesSubjectName = null;
// UUID of the pre-built PDF on the server (returned by /generate-notes).
// Cleared when the user views updated notes so the next download re-generates.
let currentNotesFileId = null;

function slugifyFilename(name) {
    const slug = (name || "").toLowerCase().trim().replace(/[^a-z0-9]+/g, "-").replace(/(^-+|-+$)/g, "");
    return slug || "study-notes";
}

document.getElementById("upload-form").addEventListener("submit", async (e) => {
    e.preventDefault();

    const fileInput   = document.getElementById("file-input");
    const notesOutput = document.getElementById("notes-output");

    if (!fileInput.files.length) {
        notesOutput.textContent = "Please select a file first.";
        return;
    }

    const formData = new FormData();
    formData.append("file", fileInput.files[0]);
    notesOutput.textContent = "Generating notes…";

    try {
        const response = await fetch("/generate-notes", { method: "POST", body: formData });
        const data     = await response.json();

        if (!response.ok) {
            notesOutput.textContent = `Error: ${data.error || response.statusText}`;
            return;
        }

        notesOutput.innerHTML = marked.parse(data.notes);
        window.generatedMarkdown = data.markdown;
        currentNotesSubjectName = data.suggested_subject || null;
        currentNotesFileId = data.notes_file_id || null;
        const dlBtn = document.getElementById("download-notes-btn");
        dlBtn.style.display = "flex";
        if (window.MathJax) MathJax.typesetPromise();

        // Notes generated successfully — ask the student to confirm/edit the
        // subject this belongs to (or type one, if the model couldn't suggest one).
        openSubjectModal(data.suggested_subject || "");
    } catch (err) {
        notesOutput.textContent = `Request failed: ${err.message}`;
    }
});

document.getElementById("download-notes-btn").addEventListener("click", async () => {
    const markdown = window.generatedMarkdown;
    if (!markdown) return;

    const dlBtn   = document.getElementById("download-notes-btn");
    const label   = document.getElementById("download-notes-label");
    const original = label.textContent;

    // Fast path: PDF was pre-built on the server during notes generation.
    if (currentNotesFileId) {
        const a    = document.createElement("a");
        a.href     = `/notes/${currentNotesFileId}/download`;
        a.download = `${slugifyFilename(currentNotesSubjectName)}-notes.pdf`;
        a.click();
        return;
    }

    // Fallback: build PDF on-demand (e.g. after notes were updated).
    dlBtn.disabled = true;
    label.textContent = "Generating PDF…";

    try {
        const response = await fetch("/download-notes-pdf", {
            method:  "POST",
            headers: { "Content-Type": "application/json" },
            body:    JSON.stringify({ notes: markdown, title: currentNotesSubjectName || "Study Notes" }),
        });

        if (!response.ok) throw new Error("PDF generation failed");

        const blob = await response.blob();
        const url  = URL.createObjectURL(blob);
        const a    = document.createElement("a");
        a.href     = url;
        a.download = `${slugifyFilename(currentNotesSubjectName)}-notes.pdf`;
        a.click();
        URL.revokeObjectURL(url);
    } catch (err) {
        label.textContent = "Error — try again";
        setTimeout(() => { label.textContent = original; }, 2500);
        return;
    } finally {
        dlBtn.disabled = false;
    }
    label.textContent = original;
});

// ── Socratic Session ──────────────────────────────────────────────────────────

document.getElementById("start-socratic-btn").addEventListener("click", async () => {
    const fileInput   = document.getElementById("socratic-file-input");
    const startBtn    = document.getElementById("start-socratic-btn");
    const loadingText = document.getElementById("socratic-loading-text");

    if (!fileInput.files.length) {
        startBtn.textContent = "Please select a file first.";
        return;
    }

    startBtn.disabled = true;
    loadingText.classList.remove("hidden");

    const formData = new FormData();
    formData.append("file", fileInput.files[0]);

    try {
        const response = await fetch("/generate-socratic", { method: "POST", body: formData });
        const data     = await response.json();

        if (!response.ok) {
            startBtn.textContent = "Error — try again";
            startBtn.disabled    = false;
            loadingText.classList.add("hidden");
            return;
        }

        socraticGoals = data.goals;
        goalIndex     = 0;
        pairIndex     = 0;

        document.getElementById("socratic-setup").classList.add("hidden");
        document.getElementById("socratic-active").classList.remove("hidden");
        showSocraticGoal();
    } catch (err) {
        startBtn.textContent = "Request failed — try again";
        startBtn.disabled    = false;
        loadingText.classList.add("hidden");
    }
});

function showSocraticGoal() {
    pairIndex = 0;
    const goal = socraticGoals[goalIndex];
    document.getElementById("soc-goal-badge-num").textContent = goalIndex + 1;
    document.getElementById("soc-goal-title").textContent     = goal.goal;
    document.getElementById("socratic-goal-header").classList.remove("hidden");
    document.getElementById("socratic-question-state").classList.remove("hidden");
    document.getElementById("socratic-insight-state").classList.add("hidden");
    document.getElementById("socratic-done-state").classList.add("hidden");
    showSocraticQuestion();
}

function showSocraticQuestion() {
    const goal = socraticGoals[goalIndex];
    const pair = goal.pairs[pairIndex];

    document.getElementById("soc-goal-current").textContent = goalIndex + 1;
    document.getElementById("soc-goal-total").textContent   = socraticGoals.length;
    document.getElementById("soc-q-label").textContent      = `Question ${pairIndex + 1} of ${goal.pairs.length}`;

    document.getElementById("soc-question-text").textContent = pair.question;
    document.getElementById("soc-answer-text").textContent   = pair.answer;
    document.getElementById("soc-answer").classList.add("hidden");
    document.getElementById("soc-reveal-btn").classList.remove("hidden");
    document.getElementById("soc-next-btn").classList.add("hidden");
}

document.getElementById("soc-reveal-btn").addEventListener("click", () => {
    document.getElementById("soc-answer").classList.remove("hidden");
    document.getElementById("soc-reveal-btn").classList.add("hidden");
    document.getElementById("soc-next-btn").classList.remove("hidden");
});

document.getElementById("soc-next-btn").addEventListener("click", () => {
    const goal = socraticGoals[goalIndex];
    pairIndex++;
    if (pairIndex >= goal.pairs.length) {
        showSocraticInsight();
    } else {
        showSocraticQuestion();
    }
});

function showSocraticInsight() {
    const goal   = socraticGoals[goalIndex];
    const isLast = goalIndex >= socraticGoals.length - 1;

    document.getElementById("soc-goal-current").textContent  = goalIndex + 1;
    document.getElementById("soc-goal-total").textContent    = socraticGoals.length;
    document.getElementById("soc-q-label").textContent       = "Complete";
    document.getElementById("soc-insight-text").textContent  = goal.goal_description;
    document.getElementById("soc-next-goal-btn").textContent = isLast ? "Finish" : "Next Goal →";

    document.getElementById("socratic-question-state").classList.add("hidden");
    document.getElementById("socratic-insight-state").classList.remove("hidden");
    document.getElementById("socratic-done-state").classList.add("hidden");
}

document.getElementById("soc-next-goal-btn").addEventListener("click", () => {
    goalIndex++;
    if (goalIndex >= socraticGoals.length) {
        showSocraticDone();
    } else {
        showSocraticGoal();
    }
});

function showSocraticDone() {
    document.getElementById("soc-goal-current").textContent = socraticGoals.length;
    document.getElementById("soc-goal-total").textContent   = socraticGoals.length;
    document.getElementById("soc-q-label").textContent      = "";
    document.getElementById("socratic-goal-header").classList.add("hidden");
    document.getElementById("socratic-question-state").classList.add("hidden");
    document.getElementById("socratic-insight-state").classList.add("hidden");
    document.getElementById("socratic-done-state").classList.remove("hidden");
}

document.getElementById("soc-restart-btn").addEventListener("click", () => {
    socraticGoals = [];
    goalIndex     = 0;
    pairIndex     = 0;
    document.getElementById("socratic-active").classList.add("hidden");
    document.getElementById("socratic-setup").classList.remove("hidden");
    const startBtn = document.getElementById("start-socratic-btn");
    startBtn.textContent = "Start Socratic Session";
    startBtn.disabled    = false;
    document.getElementById("socratic-loading-text").classList.add("hidden");
});

// ── Quiz Me ───────────────────────────────────────────────────────────────────

let quizPairs      = [];
let quizIndex      = 0;
let quizScore      = 0;
let quizStreak     = 0;
let followUpPairs          = [];
let followUpIndex          = 0;
let isFollowUp             = false;
let originalMissedQuestion = "";
let lastCheckData  = null;
let lastStudentAns = "";
let missedQuestions = [];
let _updatedNotes   = "";
// Captured when the quiz starts, so a subject created/switched elsewhere
// mid-quiz can't retarget where this session's result gets recorded.
let quizSubjectId = null;

function updateQuizStats(answered) {
    const remaining = quizPairs.length - answered;
    document.getElementById("quiz-stat-correct").textContent   = quizScore;
    document.getElementById("quiz-stat-of").textContent        = `of ${answered} answered`;
    document.getElementById("quiz-stat-streak").textContent    = `🔥 ${quizStreak}`;
    document.getElementById("quiz-stat-remaining").textContent = Math.max(0, remaining);

    const dotsEl = document.getElementById("streak-dots");
    dotsEl.innerHTML = "";
    for (let i = 0; i < 10; i++) {
        const dot = document.createElement("div");
        dot.style.cssText = `width:7px;height:7px;border-radius:50%;background:${i < quizScore ? "#1D9E75" : "#e8eaed"};`;
        dotsEl.appendChild(dot);
    }
    const label = document.createElement("span");
    label.style.cssText = "font-size:12px;color:#9ca3af;margin-left:7px;";
    label.textContent = `${quizStreak} correct streak`;
    dotsEl.appendChild(label);
}

document.getElementById("start-quiz-btn").addEventListener("click", async () => {
    const fileInput   = document.getElementById("quiz-file-input");
    const startBtn    = document.getElementById("start-quiz-btn");
    const loadingText = document.getElementById("quiz-loading-text");

    if (!fileInput.files.length) {
        startBtn.textContent = "Please select a file first.";
        return;
    }

    startBtn.disabled = true;
    loadingText.classList.remove("hidden");

    const formData = new FormData();
    formData.append("file", fileInput.files[0]);

    try {
        const response = await fetch("/generate-quiz", { method: "POST", body: formData });
        const data     = await response.json();

        if (!response.ok) {
            startBtn.textContent = "Error — try again";
            startBtn.disabled    = false;
            loadingText.classList.add("hidden");
            return;
        }

        quizPairs     = data.pairs;
        quizIndex     = 0;
        quizScore     = 0;
        quizStreak    = 0;
        quizSubjectId = getActiveSubjectId();

        document.getElementById("quiz-setup").classList.add("hidden");
        document.getElementById("quiz-active").classList.remove("hidden");
        updateQuizStats(0);
        showQuizQuestion(0);
    } catch (err) {
        startBtn.textContent = "Request failed — try again";
        startBtn.disabled    = false;
        loadingText.classList.add("hidden");
    }
});

function updateQuizProgress(index) {
    document.getElementById("quiz-q-current").textContent = index + 1;
    document.getElementById("quiz-q-total").textContent   = quizPairs.length;
    document.getElementById("quiz-progress-fill").style.width =
        `${(index / quizPairs.length) * 100}%`;
}

function showQuizQuestion(index) {
    const backBtn = document.getElementById("back-to-main-quiz");
    const origBox = document.getElementById("followup-original-question");

    let pair;
    if (isFollowUp) {
        pair = followUpPairs[followUpIndex];
        document.getElementById("quiz-q-current").textContent = followUpIndex + 1;
        document.getElementById("quiz-q-total").textContent   = followUpPairs.length;
        document.getElementById("quiz-progress-fill").style.width =
            `${(followUpIndex / followUpPairs.length) * 100}%`;
        document.getElementById("quiz-type-badge").textContent = `Follow-up · ${pair.question_type}`;
        backBtn.classList.remove("hidden");
        origBox.classList.remove("hidden");
        document.getElementById("original-missed-question-text").textContent = originalMissedQuestion;
    } else {
        pair = quizPairs[index];
        updateQuizProgress(index);
        document.getElementById("quiz-type-badge").textContent = pair.question_type;
        backBtn.classList.add("hidden");
        origBox.classList.add("hidden");
    }

    document.getElementById("quiz-question-text").textContent = pair.question;
    document.getElementById("quiz-answer-input").value        = "";

    const submitBtn = document.getElementById("submit-answer-btn");
    submitBtn.textContent = "Submit Answer";
    submitBtn.disabled    = false;
    document.getElementById("quiz-grading-text").classList.add("hidden");

    document.getElementById("quiz-question-state").classList.remove("hidden");
    document.getElementById("quiz-feedback-state").classList.add("hidden");
    document.getElementById("quiz-results-state").classList.add("hidden");

    if (window.MathJax) MathJax.typesetPromise([document.getElementById("quiz-question-state")]);
}

document.getElementById("submit-answer-btn").addEventListener("click", async () => {
    const studentAnswer = document.getElementById("quiz-answer-input").value.trim();
    const submitBtn     = document.getElementById("submit-answer-btn");
    const gradingText   = document.getElementById("quiz-grading-text");

    if (!studentAnswer) return;

    submitBtn.disabled    = true;
    submitBtn.textContent = "Grading…";
    gradingText.classList.remove("hidden");

    try {
        let response;
        if (isFollowUp) {
            const pair = followUpPairs[followUpIndex];
            response = await fetch("/check-followup", {
                method:  "POST",
                headers: { "Content-Type": "application/json" },
                body:    JSON.stringify({
                    question:       pair.question,
                    true_answer:    pair.answer,
                    student_answer: studentAnswer,
                }),
            });
        } else {
            response = await fetch("/check-answer", {
                method:  "POST",
                headers: { "Content-Type": "application/json" },
                body:    JSON.stringify({ question_index: quizIndex, student_answer: studentAnswer }),
            });
        }
        const data = await response.json();

        if (!response.ok) {
            submitBtn.textContent = "Error — try again";
            submitBtn.disabled    = false;
            gradingText.classList.add("hidden");
            return;
        }

        if (data.result && !isFollowUp) quizScore++;
        lastCheckData  = data;
        lastStudentAns = studentAnswer;
        showQuizFeedback(studentAnswer, data);
    } catch (err) {
        submitBtn.textContent = "Request failed — try again";
        submitBtn.disabled    = false;
        gradingText.classList.add("hidden");
    }
});

function showQuizFeedback(studentAnswer, data) {
    if (!isFollowUp) {
        if (data.result) { quizStreak++; } else { quizStreak = 0; }
        updateQuizStats(quizIndex + 1);
    }

    const pair = isFollowUp ? followUpPairs[followUpIndex] : quizPairs[quizIndex];

    document.getElementById("quiz-fb-type-badge").textContent =
        isFollowUp ? `Follow-up · ${pair.question_type}` : pair.question_type;
    document.getElementById("quiz-fb-question-text").textContent    = pair.question;
    document.getElementById("quiz-student-answer-text").textContent = studentAnswer;
    document.getElementById("quiz-analysis-text").textContent       = data.analysis;
    document.getElementById("quiz-true-answer-text").textContent    = pair.answer;
    document.getElementById("quiz-true-answer-box").classList.add("hidden");

    const feedbackBox = document.getElementById("quiz-feedback-box");
    feedbackBox.classList.toggle("correct",   data.result);
    feedbackBox.classList.toggle("incorrect", !data.result);

    const prefix = data.result ? "✓" : "✗";
    const rubricList = document.getElementById("quiz-rubric-list");
    rubricList.innerHTML = "";
    data.rubric.forEach((item) => {
        const li = document.createElement("li");
        li.className = "quiz-rubric-item " + (data.result ? "rubric-pass" : "rubric-fail");
        li.textContent = `${prefix} ${item}`;
        rubricList.appendChild(li);
    });

    // Track missed main-quiz questions for notes update
    if (!data.result && !isFollowUp) {
        missedQuestions.push({
            question_index: quizIndex,
            student_answer: studentAnswer,
            evaluation:     data.evaluation,
        });
    }

    // Follow-up button: only on main questions, clickable only when wrong
    const followupBtn = document.getElementById("quiz-followup-btn");
    followupBtn.classList.toggle("hidden", isFollowUp);
    if (!isFollowUp) {
        followupBtn.disabled    = data.result;
        followupBtn.textContent = "Follow-Up Questions";
    }

    document.getElementById("back-to-main-from-result").classList.toggle("hidden", !isFollowUp);

    // Next button label
    let nextText;
    if (isFollowUp) {
        nextText = followUpIndex >= followUpPairs.length - 1 ? "Back to Quiz" : "Next Question";
    } else {
        nextText = quizIndex >= quizPairs.length - 1 ? "See Results" : "Next Question";
    }
    document.getElementById("quiz-next-btn").textContent = nextText;

    document.getElementById("quiz-question-state").classList.add("hidden");
    document.getElementById("quiz-feedback-state").classList.remove("hidden");
    document.getElementById("quiz-results-state").classList.add("hidden");

    if (window.MathJax) MathJax.typesetPromise([document.getElementById("quiz-feedback-state")]);
}

document.getElementById("quiz-see-answer-btn").addEventListener("click", () => {
    const box = document.getElementById("quiz-true-answer-box");
    box.classList.remove("hidden");
    if (window.MathJax) MathJax.typesetPromise([box]);
});

document.getElementById("quiz-followup-btn").addEventListener("click", async () => {
    const followupBtn = document.getElementById("quiz-followup-btn");
    followupBtn.textContent = "Loading…";
    followupBtn.disabled    = true;

    try {
        const response = await fetch("/followup-questions", {
            method:  "POST",
            headers: { "Content-Type": "application/json" },
            body:    JSON.stringify({
                question_index: quizIndex,
                student_answer: lastStudentAns,
                evaluation:     lastCheckData.evaluation,
            }),
        });
        const data = await response.json();

        if (!response.ok) {
            followupBtn.textContent = "Error — try again";
            followupBtn.disabled    = false;
            return;
        }

        originalMissedQuestion = quizPairs[quizIndex].question;
        followUpPairs = data.pairs;
        followUpIndex = 0;
        isFollowUp    = true;
        showQuizQuestion(quizIndex);
    } catch (err) {
        followupBtn.textContent = "Error — try again";
        followupBtn.disabled    = false;
    }
});

function returnToMainQuiz() {
    isFollowUp    = false;
    followUpPairs = [];
    followUpIndex = 0;
    showQuizQuestion(quizIndex);
}

document.getElementById("back-to-main-quiz").addEventListener("click", returnToMainQuiz);
document.getElementById("back-to-main-from-result").addEventListener("click", returnToMainQuiz);

document.getElementById("quiz-next-btn").addEventListener("click", () => {
    if (isFollowUp) {
        followUpIndex++;
        if (followUpIndex >= followUpPairs.length) {
            isFollowUp    = false;
            followUpPairs = [];
            followUpIndex = 0;
            quizIndex++;
            if (quizIndex >= quizPairs.length) {
                showQuizResults();
            } else {
                showQuizQuestion(quizIndex);
            }
        } else {
            showQuizQuestion(quizIndex);
        }
    } else {
        if (quizIndex >= quizPairs.length - 1) {
            showQuizResults();
        } else {
            quizIndex++;
            showQuizQuestion(quizIndex);
        }
    }
});

function showQuizResults() {
    document.getElementById("quiz-progress-fill").style.width = "100%";
    document.getElementById("quiz-feedback-state").classList.add("hidden");
    document.getElementById("quiz-results-state").classList.remove("hidden");

    const accuracy = quizPairs.length > 0 ? quizScore / quizPairs.length : 1;
    document.getElementById("quiz-score-text").textContent =
        `You got ${quizScore} out of ${quizPairs.length} correct.`;

    // Weak-topic flag — a session below 40% accuracy is treated as a weak-topic signal.
    const isWeak = accuracy < 0.4 && missedQuestions.length > 0;
    document.getElementById("quiz-weak-banner").classList.toggle("hidden", !isWeak);

    // Record this session against whichever subject was active when the quiz
    // started (captured in quizSubjectId). recordQuizResult is now async (hits
    // the server), so we fire it without awaiting — the hint toggles on whether
    // a subject was selected, not on whether the server call succeeds.
    document.getElementById("quiz-no-subject-hint").classList.toggle("hidden", !!quizSubjectId);
    if (quizSubjectId) {
        recordQuizResult({ subjectId: quizSubjectId, correct: quizScore, total: quizPairs.length });
    }

    // Reset update-notes section; rename button to signal the weak-topic connection
    const updateBtn = document.getElementById("quiz-update-notes-btn");
    updateBtn.textContent = isWeak ? "Reinforce Notes (Weak Topic)" : "Update Notes";
    updateBtn.disabled    = false;
    document.getElementById("quiz-notes-update-status").classList.add("hidden");
    document.getElementById("quiz-view-notes-link").classList.add("hidden");

    if (missedQuestions.length === 0) {
        document.getElementById("quiz-notes-update-wrap").classList.add("hidden");
        document.getElementById("quiz-all-correct-msg").classList.remove("hidden");
    } else {
        document.getElementById("quiz-notes-update-wrap").classList.remove("hidden");
        document.getElementById("quiz-all-correct-msg").classList.add("hidden");
    }
}

document.getElementById("quiz-update-notes-btn").addEventListener("click", async () => {
    const updateBtn   = document.getElementById("quiz-update-notes-btn");
    const statusDiv   = document.getElementById("quiz-notes-update-status");
    const statusText  = document.getElementById("quiz-notes-update-text");
    const viewLink    = document.getElementById("quiz-view-notes-link");

    updateBtn.textContent = "Updating notes…";
    updateBtn.disabled    = true;

    try {
        const response = await fetch("/update-notes", {
            method:  "POST",
            headers: { "Content-Type": "application/json" },
            body:    JSON.stringify({ missed: missedQuestions }),
        });
        const data = await response.json();

        if (!response.ok) {
            updateBtn.textContent = "Error — try again";
            updateBtn.disabled    = false;
            return;
        }

        _updatedNotes = data.notes;
        const n = data.count;
        updateBtn.classList.add("hidden");
        statusDiv.classList.remove("hidden");
        statusText.textContent = `Notes updated — ${n} concept${n === 1 ? "" : "s"} reinforced.`;
        viewLink.classList.remove("hidden");
    } catch (err) {
        updateBtn.textContent = "Error — try again";
        updateBtn.disabled    = false;
    }
});

document.getElementById("quiz-view-notes-link").addEventListener("click", (e) => {
    e.preventDefault();
    switchTab("notes");
    if (_updatedNotes) {
        document.getElementById("notes-output").innerHTML = marked.parse(_updatedNotes);
        window.generatedMarkdown = _updatedNotes;
        currentNotesFileId = null; // pre-built PDF is stale; next download re-generates
    }
});

document.getElementById("quiz-restart-btn").addEventListener("click", () => {
    quizPairs       = [];
    quizIndex       = 0;
    quizScore       = 0;
    followUpPairs   = [];
    followUpIndex   = 0;
    isFollowUp      = false;
    lastCheckData   = null;
    lastStudentAns  = "";
    missedQuestions = [];
    _updatedNotes   = "";
    document.getElementById("quiz-active").classList.add("hidden");
    document.getElementById("quiz-setup").classList.remove("hidden");
    const startBtn = document.getElementById("start-quiz-btn");
    startBtn.textContent = "Start Quiz";
    startBtn.disabled    = false;
    document.getElementById("quiz-loading-text").classList.add("hidden");
});

// ── Course page ───────────────────────────────────────────────────────────────

function renderCoursePage(subjectId) {
    const content = document.getElementById("course-page-content");
    const meta = getSubjects().find(s => s.id === subjectId);

    if (!meta) {
        content.innerHTML = `
            <div class="course-page-wrap">
                <h1 style="font-size:22px;font-weight:600;color:#1a1a1a;margin-bottom:24px;">Subject</h1>
                <div class="cpage-empty">
                    <p>No data found for this subject.</p>
                </div>
            </div>`;
        return;
    }

    const summary = getSubjectProgress(subjectId);
    const lastQuiz = summary.accuracyHistory.length
        ? summary.accuracyHistory[summary.accuracyHistory.length - 1]
        : null;
    const lastAccText = lastQuiz ? `Last quiz: ${lastQuiz.accuracy}%` : "No quizzes yet";

    content.innerHTML = `
        <div class="course-page-wrap">
            <div class="cpage-header">
                <div>
                    <h1 class="cpage-title">${meta.name}</h1>
                    <p class="cpage-subtitle">${lastAccText} · ${summary.streak.current}-day streak</p>
                </div>
                <div class="cpage-quick-stats">
                    <span class="cpage-streak">🔥 ${summary.streak.current}</span>
                </div>
            </div>

            <div class="cpage-actions">
                <button class="cpage-action-btn cpage-action-primary" data-action-tab="quiz">
                    <i class="ti ti-brain"></i> Start Quiz
                </button>
                <button class="cpage-action-btn" data-action-tab="notes">
                    <i class="ti ti-file-text"></i> Create Notes
                </button>
                <button class="cpage-action-btn" data-action-tab="socratic">
                    <i class="ti ti-messages"></i> Socratic Q&A
                </button>
            </div>

            <button class="cpage-progress-link" data-progress-subject="${subjectId}">
                View full progress →
            </button>
        </div>`;

    // Action buttons → navigate to the right tab
    content.querySelectorAll("[data-action-tab]").forEach(btn => {
        btn.addEventListener("click", () => switchTab(btn.dataset.actionTab));
    });

    // "View full progress" → switch to Progress and pin to this subject
    content.querySelector("[data-progress-subject]").addEventListener("click", (e) => {
        setActiveSubject(e.currentTarget.dataset.progressSubject);
        switchTab("progress");
    });
}

// ── Progress page ─────────────────────────────────────────────────────────────

// Static metadata (icon/label/description) for each achievement key stored
// per-subject — "earned" is filled in from real data below.
const ACHIEVEMENT_META = [
    { key: "firstQuiz",      label: "First Quiz",     desc: "Completed your first quiz",                          icon: "🎯" },
    { key: "threeDayStreak", label: "3-Day Streak",   desc: "Studied 3 days in a row",                             icon: "🔥" },
    { key: "highAchiever",   label: "High Achiever",  desc: "Averaged 90%+ accuracy across your last 5 quizzes",  icon: "🏅" },
    { key: "sevenDayStreak", label: "7-Day Streak",   desc: "Study 7 days in a row to unlock",                     icon: "⚡" },
    { key: "comeback",       label: "Comeback",       desc: "Returned after breaking a streak",                    icon: "💪" },
    { key: "perfectionist",  label: "Perfectionist",  desc: "Score 100% on a quiz",                                icon: "⭐" },
];

// "YYYY-MM-DD" → short label ("Jul 8") for the chart's X axis.
function formatShortDate(iso) {
    const [y, m, d] = iso.split("-").map(Number);
    const date = new Date(y, m - 1, d);
    return `${date.toLocaleString("default", { month: "short" })} ${date.getDate()}`;
}

// Renders the subject tabs atop the Progress page from real data.
function renderSubjectTabs(activeId) {
    const container = document.getElementById("prog-subject-switcher");
    const subjects = getSubjects();

    if (!subjects.length) {
        container.innerHTML = "";
        return;
    }

    container.innerHTML = subjects.map(s => `
        <button class="prog-course-btn ${s.id === activeId ? "active" : ""}" data-subject="${s.id}">
            ${s.name}
        </button>`).join("");

    container.querySelectorAll("[data-subject]").forEach(btn => {
        btn.addEventListener("click", () => {
            setActiveSubject(btn.dataset.subject);
            renderProgress(btn.dataset.subject);
        });
    });
}

// subjectId is optional — defaults to the active subject, falling back to
// the first subject that exists (e.g. on first-ever visit to this tab).
function renderProgress(subjectId) {
    const subjects = getSubjects();
    const emptyState = document.getElementById("prog-empty-state");
    const content     = document.getElementById("prog-content");

    if (!subjects.length) {
        renderSubjectTabs(null);
        emptyState.classList.remove("hidden");
        content.classList.add("hidden");
        return;
    }

    let resolvedId = subjectId || getActiveSubjectId();
    if (!resolvedId || !subjects.some(s => s.id === resolvedId)) {
        resolvedId = subjects[0].id;
    }
    setActiveSubject(resolvedId);

    emptyState.classList.add("hidden");
    content.classList.remove("hidden");
    renderSubjectTabs(resolvedId);

    const summary = getSubjectProgress(resolvedId);

    // Streak
    document.getElementById("prog-streak-val").textContent  = summary.streak.current;
    document.getElementById("prog-longest-val").textContent = summary.streak.best;
    document.getElementById("prog-streak-icon").classList.toggle("glowing", summary.streak.current > 0);

    // Chart — most recent ~10 points keep it readable.
    renderAccuracyChart(summary.accuracyHistory.slice(-10).map(h => ({
        date: formatShortDate(h.date),
        accuracy: h.accuracy,
    })));

    // Badges
    const badgesEl = document.getElementById("prog-badges");
    badgesEl.innerHTML = ACHIEVEMENT_META.map(m => {
        const earned = !!summary.achievements[m.key];
        return `
            <div class="prog-badge ${earned ? "earned" : "unearned"}">
                <div class="prog-badge-icon">${m.icon}</div>
                <div class="prog-badge-label">${m.label}</div>
                <div class="prog-badge-tooltip">${earned ? "✓ " : ""}${m.desc}</div>
            </div>`;
    }).join("");
}

function renderAccuracyChart(history) {
    const svg = document.getElementById("prog-chart");
    const W = 580, H = 190;
    const padL = 40, padR = 18, padT = 22, padB = 36;
    const cW = W - padL - padR;
    const cH = H - padT - padB;

    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    svg.setAttribute("preserveAspectRatio", "xMidYMid meet");

    if (!history || !history.length) {
        svg.innerHTML = `<text x="${W/2}" y="${H/2}" text-anchor="middle" fill="#9ca3af" font-family="Inter,sans-serif" font-size="13">No quiz history yet</text>`;
        return;
    }

    const n   = history.length;
    const toX = i => n > 1 ? padL + (i / (n - 1)) * cW : padL + cW / 2;
    const toY = v => padT + cH * (1 - v / 100);

    let s = "";

    // Horizontal grid lines + Y labels
    [0, 25, 50, 75, 100].forEach(v => {
        const y = toY(v).toFixed(1);
        s += `<line x1="${padL}" y1="${y}" x2="${W - padR}" y2="${y}" stroke="${v === 0 ? "#d1d5db" : "#f0f0f0"}" stroke-width="1"/>`;
        s += `<text x="${padL - 6}" y="${(parseFloat(y) + 4).toFixed(1)}" text-anchor="end" fill="#9ca3af" font-size="10" font-family="Inter,sans-serif">${v}%</text>`;
    });

    // Shaded fill
    const pts    = history.map((p, i) => `${toX(i).toFixed(1)},${toY(p.accuracy).toFixed(1)}`).join(" ");
    const baseY  = toY(0).toFixed(1);
    s += `<polygon points="${toX(0).toFixed(1)},${baseY} ${pts} ${toX(n-1).toFixed(1)},${baseY}" fill="rgba(153,0,0,0.07)"/>`;

    // Line
    s += `<polyline points="${pts}" fill="none" stroke="#990000" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round"/>`;

    // Dots + labels
    history.forEach((p, i) => {
        const x = toX(i).toFixed(1);
        const y = toY(p.accuracy).toFixed(1);
        // X-axis date
        s += `<text x="${x}" y="${H - 5}" text-anchor="middle" fill="#9ca3af" font-size="10" font-family="Inter,sans-serif">${p.date}</text>`;
        // Accuracy label above dot
        s += `<text x="${x}" y="${(parseFloat(y) - 10).toFixed(1)}" text-anchor="middle" fill="#6b7280" font-size="10" font-weight="600" font-family="Inter,sans-serif">${p.accuracy}%</text>`;
        // Dot
        s += `<circle cx="${x}" cy="${y}" r="5" fill="white" stroke="#990000" stroke-width="2.5"/>`;
        s += `<circle cx="${x}" cy="${y}" r="2" fill="#990000"/>`;
    });

    svg.innerHTML = s;
}
