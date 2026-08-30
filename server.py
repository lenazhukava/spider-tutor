import fcntl
import os
import re
import secrets
import sys
import tempfile
import uuid as _uuid

# Load .env into the environment if it exists (dev convenience).
_env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
if os.path.exists(_env_path):
    with open(_env_path) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip())

import yaml
from flask import (
    Flask, render_template, request, jsonify,
    Response, session, redirect, url_for, send_file,
)

import db
from pdf_export import build_notes_pdf

TUTOR_NEW_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tutor_new")
sys.path.insert(0, TUTOR_NEW_DIR)
from tutoring_module import (
    create_draft_notes,
    create_study_goals,
    create_socratic_qa,
    create_sample_test_qa,
    check_answer as grade_answer,
    create_followup_testing_qa,
    update_notes as apply_note_patch,
)

app = Flask(__name__)
# Read secret key from env for production; fall back to a random value for local dev.
# Set FLASK_SECRET_KEY to a stable value in any environment beyond local testing,
# otherwise sessions are invalidated every time the server restarts.
app.secret_key = os.environ.get("FLASK_SECRET_KEY") or secrets.token_hex(32)

db.init_db()

# ── Auth helpers ──────────────────────────────────────────────────────────────

USERS_YAML = os.path.join(os.path.dirname(os.path.abspath(__file__)), "users.yaml")


def load_users() -> dict[str, str]:
    """Re-read users.yaml from disk so edits take effect without a server restart."""
    try:
        with open(USERS_YAML) as f:
            data = yaml.safe_load(f) or {}
        return {str(k): str(v) for k, v in data.items()}
    except FileNotFoundError:
        return {}


def _write_new_user(username: str, password: str) -> str | None:
    """
    Exclusive-lock read-modify-write of users.yaml.
    Returns an error string on failure, or None on success.
    os.O_CREAT | os.O_RDWR opens atomically (creates if missing, never truncates),
    and fcntl.LOCK_EX serialises concurrent signups at the OS level.
    """
    fd = os.open(USERS_YAML, os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(fd, "r+") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            users = yaml.safe_load(f.read()) or {}
        except yaml.YAMLError:
            users = {}
        users = {str(k): str(v) for k, v in users.items()}

        if username.lower() in {k.lower() for k in users}:
            return f'Username "{username}" is already taken.'

        users[username] = password
        f.seek(0)
        f.truncate()
        yaml.dump(users, f, default_flow_style=False, allow_unicode=True)
    return None


@app.before_request
def require_login():
    if request.endpoint in ("login", "logout", "signup", "static"):
        return
    if not session.get("username"):
        return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("username"):
        return redirect(url_for("index"))
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        users = load_users()
        if username and users.get(username) == password:
            session["username"] = username
            return redirect(url_for("index"))
        error = "Invalid username or password."
    return render_template("login.html", error=error)


@app.route("/signup", methods=["GET", "POST"])
def signup():
    if session.get("username"):
        return redirect(url_for("index"))
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        confirm  = request.form.get("confirm_password", "")
        if not username:
            error = "Username cannot be empty."
        elif not password:
            error = "Password cannot be empty."
        elif password != confirm:
            error = "Passwords do not match."
        else:
            error = _write_new_user(username, password)
            if error is None:
                session["username"] = username
                return redirect(url_for("index"))
    return render_template("signup.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# Single-user in-memory store (dev tool — no concurrent sessions needed)
_quiz_pairs: list[dict] = []
_last_notes: str = ""
_quiz_file_path: str = ""  # persisted across requests so update-notes can find the source file


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/generate-notes", methods=["POST"])
def generate_notes():
    global _last_notes
    file = request.files.get("file")
    if not file or file.filename == "":
        return jsonify({"error": "No file uploaded"}), 400

    with tempfile.TemporaryDirectory() as tmpdir:
        filepath = os.path.join(tmpdir, file.filename)
        file.save(filepath)

        orig_dir = os.getcwd()
        try:
            os.chdir(TUTOR_NEW_DIR)
            result = create_draft_notes(class_material_files=[filepath])
        finally:
            os.chdir(orig_dir)

    _last_notes = result.notes

    # Pre-build a PDF and save it so /notes/<id>/download works instantly.
    notes_file_id = None
    try:
        pdf_bytes = build_notes_pdf(result.notes, title=result.suggested_subject or "Study Notes")
        notes_file_id = str(_uuid.uuid4())
        fname = f"{_safe_filename(result.suggested_subject)}-notes.pdf"
        file_path = os.path.join(db.NOTES_DIR, f"{notes_file_id}.pdf")
        with open(file_path, "wb") as fh:
            fh.write(pdf_bytes)
        db.insert_notes_file(notes_file_id, session["username"], file_path, fname)
    except Exception:
        notes_file_id = None

    return jsonify({
        "notes": result.notes,
        "markdown": result.notes,
        "thought_process": result.thought_process,
        "suggested_subject": result.suggested_subject,
        "notes_file_id": notes_file_id,
    })


def _safe_filename(name: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", name or "").strip("-").lower()
    return slug or "study-notes"


@app.route("/download-notes-pdf", methods=["POST"])
def download_notes_pdf():
    data  = request.get_json(silent=True) or {}
    notes = data.get("notes", "")
    title = data.get("title") or "Study Notes"

    if not notes:
        return jsonify({"error": "No notes provided"}), 400

    pdf_bytes = build_notes_pdf(notes, title=title)
    filename = f"{_safe_filename(title)}-notes.pdf"

    return Response(
        pdf_bytes,
        mimetype="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.route("/generate-socratic", methods=["POST"])
def generate_socratic():
    file = request.files.get("file")
    if not file or file.filename == "":
        return jsonify({"error": "No file uploaded"}), 400

    with tempfile.TemporaryDirectory() as tmpdir:
        filepath = os.path.join(tmpdir, file.filename)
        file.save(filepath)

        orig_dir = os.getcwd()
        try:
            os.chdir(TUTOR_NEW_DIR)
            goals = create_study_goals(class_material_files=[filepath])
            result_goals = []
            for goal in goals.goals:
                qa_list = create_socratic_qa(
                    goal=goal.goal,
                    goal_description=goal.goal_description,
                    class_material_files=[filepath],
                )
                result_goals.append({
                    "goal": goal.goal,
                    "goal_description": goal.goal_description,
                    "pairs": [
                        {"question": p.question, "answer": p.answer}
                        for p in qa_list.pairs
                    ],
                })
        finally:
            os.chdir(orig_dir)

    return jsonify({"goals": result_goals})


@app.route("/generate-quiz", methods=["POST"])
def generate_quiz():
    global _quiz_pairs, _quiz_file_path
    file = request.files.get("file")
    if not file or file.filename == "":
        return jsonify({"error": "No file uploaded"}), 400

    # Clean up the previous quiz file
    if _quiz_file_path and os.path.exists(_quiz_file_path):
        try:
            os.unlink(_quiz_file_path)
        except OSError:
            pass

    # Save to a named temp file that outlives this request so update-notes can reuse it
    suffix = os.path.splitext(file.filename)[1] or ".bin"
    fd, persistent_path = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    file.save(persistent_path)
    _quiz_file_path = persistent_path

    orig_dir = os.getcwd()
    try:
        os.chdir(TUTOR_NEW_DIR)
        qa_list = create_sample_test_qa(
            list_of_question_types=["Conceptual", "True/False", "Short Answer", "Application"],
            class_material_files=[persistent_path],
        )
    finally:
        os.chdir(orig_dir)

    _quiz_pairs = [
        {"question": p.question, "answer": p.answer, "question_type": p.question_type}
        for p in qa_list.pairs
    ]
    return jsonify({"pairs": _quiz_pairs})


@app.route("/check-answer", methods=["POST"])
def check_answer_route():
    data = request.get_json()
    question_index = data.get("question_index")
    student_answer = data.get("student_answer", "")

    if not _quiz_pairs or question_index is None or question_index >= len(_quiz_pairs):
        return jsonify({"error": "No active quiz or invalid index"}), 400

    pair = _quiz_pairs[question_index]
    orig_dir = os.getcwd()
    try:
        os.chdir(TUTOR_NEW_DIR)
        result = grade_answer(
            question=pair["question"],
            true_answer=pair["answer"],
            student_answer=student_answer,
        )
    finally:
        os.chdir(orig_dir)

    return jsonify({
        "result": result.result,
        "analysis": result.analysis,
        "rubric": result.rubric,
        "evaluation": result.evaluation,
    })


@app.route("/followup-questions", methods=["POST"])
def followup_questions():
    data           = request.get_json()
    question_index = data.get("question_index")
    student_answer = data.get("student_answer", "")
    evaluation     = data.get("evaluation", "")

    if not _quiz_pairs or question_index is None or question_index >= len(_quiz_pairs):
        return jsonify({"error": "No active quiz or invalid index"}), 400

    pair = _quiz_pairs[question_index]
    orig_dir = os.getcwd()
    try:
        os.chdir(TUTOR_NEW_DIR)
        result = create_followup_testing_qa(
            question=pair["question"],
            student_answer=student_answer,
            true_answer=pair["answer"],
            evaluation=evaluation,
            list_of_question_types=["Conceptual", "Short Answer", "Application"],
        )
    finally:
        os.chdir(orig_dir)

    return jsonify({
        "pairs": [
            {"question": p.question, "answer": p.answer, "question_type": p.question_type}
            for p in result.pairs
        ]
    })


@app.route("/update-notes", methods=["POST"])
def update_notes_route():
    global _last_notes
    data   = request.get_json()
    missed = data.get("missed", [])

    if not missed:
        return jsonify({"error": "No missed questions provided"}), 400

    orig_dir = os.getcwd()
    try:
        os.chdir(TUTOR_NEW_DIR)
        current_notes = _last_notes

        # Auto-generate base notes from the quiz file if the user never visited Create Notes
        if not current_notes and _quiz_file_path and os.path.exists(_quiz_file_path):
            notes_result = create_draft_notes(class_material_files=[_quiz_file_path])
            current_notes = notes_result.notes

        # Build the full list of missed items, then patch notes in a single LLM call
        missed_items = []
        for item in missed:
            question_index = item.get("question_index")
            if question_index is None or question_index >= len(_quiz_pairs):
                continue
            pair = _quiz_pairs[question_index]
            missed_items.append({
                "question":      pair["question"],
                "true_answer":   pair["answer"],
                "student_answer": item.get("student_answer", ""),
                "evaluation":    item.get("evaluation", ""),
            })

        if missed_items:
            original_notes = current_notes
            result = apply_note_patch(
                notes=current_notes,
                missed_items=missed_items,
                class_material_files=[],
            )
            # Guard: if the LLM returned something far shorter than the input notes
            # (i.e. an error message instead of the actual notes), keep the originals.
            if len(result.notes) >= len(original_notes) * 0.5:
                current_notes = result.notes
            else:
                current_notes = original_notes
    finally:
        os.chdir(orig_dir)

    _last_notes = current_notes
    return jsonify({"notes": current_notes, "count": len(missed_items)})


@app.route("/check-followup", methods=["POST"])
def check_followup():
    data           = request.get_json()
    question       = data.get("question", "")
    true_answer    = data.get("true_answer", "")
    student_answer = data.get("student_answer", "")

    if not question or not true_answer:
        return jsonify({"error": "Missing question or true_answer"}), 400

    orig_dir = os.getcwd()
    try:
        os.chdir(TUTOR_NEW_DIR)
        result = grade_answer(
            question=question,
            true_answer=true_answer,
            student_answer=student_answer,
        )
    finally:
        os.chdir(orig_dir)

    return jsonify({
        "result":     result.result,
        "analysis":   result.analysis,
        "rubric":     result.rubric,
        "evaluation": result.evaluation,
    })


@app.route("/api/subjects", methods=["GET"])
def api_subjects_get():
    return jsonify({"subjects": db.get_subjects_for_user(session["username"])})


@app.route("/api/subjects", methods=["POST"])
def api_subjects_post():
    username = session["username"]
    data = request.get_json(silent=True) or {}
    subject_id = (data.get("id") or "").strip()
    name       = (data.get("name") or "").strip()
    if not subject_id or not name:
        return jsonify({"error": "id and name are required"}), 400
    db.insert_subject(subject_id, username, name)
    return jsonify({"ok": True})


@app.route("/api/subjects/<subject_id>/progress", methods=["GET"])
def api_subject_progress(subject_id):
    return jsonify(db.get_subject_progress(session["username"], subject_id))


@app.route("/api/subjects/<subject_id>/quiz-result", methods=["POST"])
def api_quiz_result(subject_id):
    username = session["username"]
    # Ownership check — subject must belong to this user.
    owned = {s["id"] for s in db.get_subjects_for_user(username)}
    if subject_id not in owned:
        return jsonify({"error": "Subject not found"}), 404

    data    = request.get_json(silent=True) or {}
    correct = int(data.get("correct", 0))
    total   = int(data.get("total", 0))
    date_str = (data.get("date") or "").strip()
    if not date_str:
        from datetime import date as _d
        date_str = _d.today().isoformat()

    return jsonify(db.record_quiz_result(username, subject_id, correct, total, date_str))


@app.route("/notes/<file_id>/download")
def download_notes_file(file_id):
    username = session["username"]
    record = db.get_notes_file(file_id)
    if not record or record["username"] != username:
        return jsonify({"error": "Not found"}), 404
    if not os.path.exists(record["file_path"]):
        return jsonify({"error": "File no longer available"}), 410
    return send_file(
        record["file_path"],
        as_attachment=True,
        download_name=record["original_filename"] or f"{file_id}.pdf",
        mimetype="application/pdf",
    )


@app.route("/subjects", methods=["GET", "POST"])
def subjects_route():
    username = session["username"]
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        subject_id = (data.get("id") or "").strip()
        name = (data.get("name") or "").strip()
        if not subject_id or not name:
            return jsonify({"error": "id and name are required"}), 400
        db.insert_subject(subject_id, username, name)
        return jsonify({"ok": True})
    else:
        return jsonify({"subjects": db.get_subjects_for_user(username)})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    app.run(debug=False, host='0.0.0.0', port=port)
