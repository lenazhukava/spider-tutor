// ── Progress tracking — server-backed, per-user ───────────────────────────────
//
// All durable state lives in SQLite (via Flask API). An in-memory cache keeps
// synchronous UI reads fast. Cache is populated eagerly on page load and
// refreshed after each quiz-result write. localStorage is not used for
// progress data — this is a clean cutover, no migration of old browser data.

// ── In-memory cache ───────────────────────────────────────────────────────────

const _cache = {
    activeSubjectId: null,
    subjects: {},   // { [id]: { name, createdAt } }
    progress: {},   // { [id]: progressShape }
};

function _zeroProgress() {
    return {
        streak: { current: 0, best: 0 },
        accuracyHistory: [],
        achievements: {
            firstQuiz: false, threeDayStreak: false, sevenDayStreak: false,
            comeback: false, perfectionist: false, highAchiever: false,
        },
    };
}

// ── Public synchronous readers ────────────────────────────────────────────────
// These read from _cache, which initProgress() fills on page load.

function getSubjects() {
    return Object.entries(_cache.subjects)
        .map(([id, s]) => ({ id, name: s.name, createdAt: s.createdAt }))
        .sort((a, b) => (a.createdAt < b.createdAt ? -1 : a.createdAt > b.createdAt ? 1 : 0))
        .map(({ id, name }) => ({ id, name }));
}

function getActiveSubjectId() {
    return _cache.activeSubjectId;
}

// Returns the cached progress shape for a subject (zero-state if not loaded yet).
function getSubjectProgress(subjectId) {
    return _cache.progress[subjectId] || _zeroProgress();
}

// ── Public writers ────────────────────────────────────────────────────────────

// Sets the active subject — UI-only state, no server call needed.
function setActiveSubject(id) {
    if (!_cache.subjects[id]) return false;
    _cache.activeSubjectId = id;
    return true;
}

// Creates a new subject: updates cache immediately (so the UI responds
// synchronously) and fires a server write in the background.
// Returns the new id so callers don't need to await.
function createSubject(name) {
    const trimmed = (name || "").trim() || "Untitled Subject";
    const id = crypto.randomUUID();

    _cache.subjects[id] = { name: trimmed, createdAt: new Date().toISOString() };
    _cache.progress[id] = _zeroProgress();
    _cache.activeSubjectId = id;

    fetch("/api/subjects", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id, name: trimmed }),
    }).catch(() => {});

    return id;
}

// Records a quiz result on the server, then refreshes the cached progress for
// this subject. Returns the updated progress object, or null on network error.
async function recordQuizResult({ subjectId, correct, total, date }) {
    if (!subjectId || !_cache.subjects[subjectId]) return null;

    const dateStr = date || todayStr();
    try {
        const res = await fetch(`/api/subjects/${subjectId}/quiz-result`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ correct, total, date: dateStr }),
        });
        if (!res.ok) return null;
        const data = await res.json();
        _cache.progress[subjectId] = data;
        return data;
    } catch (_) {
        return null;
    }
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function todayStr() {
    const d = new Date();
    const m   = String(d.getMonth() + 1).padStart(2, "0");
    const day = String(d.getDate()).padStart(2, "0");
    return `${d.getFullYear()}-${m}-${day}`;
}

// ── Bootstrap ─────────────────────────────────────────────────────────────────

// Fetches the logged-in user's subjects + their progress from the server,
// populates _cache, then triggers a sidebar re-render. Called automatically
// on page load — callers must not rely on synchronous state before this
// resolves (the sidebar shows an empty hint until then).
async function initProgress() {
    try {
        const res = await fetch("/api/subjects");
        if (!res.ok) return;
        const { subjects } = await res.json();

        for (const s of subjects) {
            _cache.subjects[s.id] = { name: s.name, createdAt: s.created_at };
        }
        if (!_cache.activeSubjectId && subjects.length) {
            _cache.activeSubjectId = subjects[0].id;
        }

        // Eager-load all subjects' progress in parallel — keeps rendering sync.
        await Promise.all(subjects.map(async (s) => {
            try {
                const pr = await fetch(`/api/subjects/${s.id}/progress`);
                if (pr.ok) _cache.progress[s.id] = await pr.json();
            } catch (_) {}
        }));
    } catch (_) {}

    // Both renderSidebarSubjects (script.js) and renderProgress (if Progress
    // tab is already visible) need to be called after the cache is ready.
    if (typeof renderSidebarSubjects === "function") renderSidebarSubjects();
    const progressPanel = document.getElementById("panel-progress");
    if (progressPanel && !progressPanel.classList.contains("hidden")) {
        if (typeof renderProgress === "function") renderProgress();
    }
}

initProgress();
