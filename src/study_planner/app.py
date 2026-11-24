from __future__ import annotations

import argparse
import os
import sqlite3
import threading
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Self

from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field


class ModuleCreate(BaseModel):
    code: str = Field(..., min_length=3, max_length=16, pattern="^[A-Za-z0-9_-]+$")
    title: str = Field(..., min_length=3, max_length=140)
    credits: int = Field(default=20, ge=5, le=60)
    semester: str = Field(default="Autumn", pattern="^(Autumn|Spring|Full Year)$")


class AssessmentCreate(BaseModel):
    module_code: str = Field(..., min_length=3, max_length=16)
    title: str = Field(..., min_length=3, max_length=160)
    weight_pct: float = Field(..., gt=0.0, le=100.0)
    due_date: date
    completed: bool = False


class AssessmentStatusUpdate(BaseModel):
    completed: bool


class TopicCreate(BaseModel):
    module_code: str = Field(..., min_length=3, max_length=16)
    topic: str = Field(..., min_length=1, max_length=200)


class ReviewUpdate(BaseModel):
    quality: int = Field(..., ge=0, le=5)


def compute_sm2_schedule(
    quality: int,
    repetitions: int,
    easiness: float,
    interval_days: int,
) -> tuple[int, float, int]:
    """Calculate next repetition count, easiness factor (>= 1.3), and interval in days."""
    if quality < 0 or quality > 5:
        raise ValueError("SM-2 quality rating must be between 0 and 5")

    if quality < 3:
        new_reps = 0
        next_interval = 1
    else:
        if repetitions == 0:
            next_interval = 1
        elif repetitions == 1:
            next_interval = 6
        else:
            next_interval = max(1, round(interval_days * easiness))
        new_reps = repetitions + 1

    new_ef = easiness + (0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02))
    new_ef = max(1.3, round(new_ef, 3))
    return new_reps, new_ef, next_interval


class PlannerDatabase:
    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self._db_path = str(db_path)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON;")
        if self._db_path != ":memory:":
            self._conn.execute("PRAGMA journal_mode = WAL;")
            self._conn.execute("PRAGMA busy_timeout = 5000;")
        self._conn.executescript(
            """
            PRAGMA foreign_keys = ON;

            CREATE TABLE IF NOT EXISTS modules (
                code TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                credits INTEGER NOT NULL,
                semester TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS assessments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                module_code TEXT NOT NULL,
                title TEXT NOT NULL,
                weight_pct REAL NOT NULL,
                due_date TEXT NOT NULL,
                completed INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY(module_code) REFERENCES modules(code) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS revision_topics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                module_code TEXT NOT NULL,
                topic TEXT NOT NULL,
                repetitions INTEGER NOT NULL DEFAULT 0,
                easiness REAL NOT NULL DEFAULT 2.5,
                interval_days INTEGER NOT NULL DEFAULT 1,
                next_review_date TEXT NOT NULL,
                FOREIGN KEY(module_code) REFERENCES modules(code) ON DELETE CASCADE
            );
            """
        )
        self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        self.close()

    def create_module(self, payload: ModuleCreate) -> dict[str, Any]:
        code = payload.code.strip().upper()
        title = payload.title.strip()
        if len(title) < 3:
            raise ValueError("Module title must contain at least 3 non-whitespace characters")
        now = datetime.now(UTC).isoformat()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO modules (code, title, credits, semester, created_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(code) DO UPDATE SET
                    title = excluded.title,
                    credits = excluded.credits,
                    semester = excluded.semester
                """,
                (code, title, payload.credits, payload.semester, now),
            )
            self._conn.commit()
            row = self._conn.execute(
                "SELECT code, title, credits, semester, created_at FROM modules WHERE code = ?",
                (code,),
            ).fetchone()
        return dict(row) if row is not None else {
            "code": code,
            "title": title,
            "credits": payload.credits,
            "semester": payload.semester,
            "created_at": now,
        }

    def list_modules(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM modules ORDER BY code ASC").fetchall()
        return [dict(r) for r in rows]

    def delete_module(self, module_code: str) -> bool:
        code = module_code.strip().upper()
        if not code:
            return False
        with self._lock:
            cur = self._conn.execute("DELETE FROM modules WHERE code = ?", (code,))
            self._conn.commit()
            return cur.rowcount > 0

    def create_assessment(self, payload: AssessmentCreate) -> dict[str, Any]:
        code = payload.module_code.strip().upper()
        title = payload.title.strip()
        if len(title) < 3:
            raise ValueError("Assessment title must contain at least 3 non-whitespace characters")
        with self._lock:
            mod = self._conn.execute(
                "SELECT code FROM modules WHERE code = ?", (code,)
            ).fetchone()
            if mod is None:
                raise KeyError(f"Module '{code}' does not exist")

            cur = self._conn.execute(
                """
                INSERT INTO assessments (module_code, title, weight_pct, due_date, completed)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    code,
                    title,
                    payload.weight_pct,
                    payload.due_date.isoformat(),
                    1 if payload.completed else 0,
                ),
            )
            self._conn.commit()
            assessment_id = int(cur.lastrowid or 0)
        return {
            "id": assessment_id,
            "module_code": code,
            "title": title,
            "weight_pct": payload.weight_pct,
            "due_date": payload.due_date.isoformat(),
            "completed": payload.completed,
        }

    def list_assessments(
        self,
        module_code: str | None = None,
        pending_only: bool = False,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if module_code:
            clauses.append("module_code = ?")
            params.append(module_code.strip().upper())
        if pending_only:
            clauses.append("completed = 0")
        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM assessments {where_sql} ORDER BY due_date ASC",
                params,
            ).fetchall()
        return [
            {
                "id": r["id"],
                "module_code": r["module_code"],
                "title": r["title"],
                "weight_pct": r["weight_pct"],
                "due_date": r["due_date"],
                "completed": bool(r["completed"]),
            }
            for r in rows
        ]

    def set_assessment_completed(self, assessment_id: int, completed: bool) -> dict[str, Any]:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE assessments SET completed = ? WHERE id = ?",
                (1 if completed else 0, assessment_id),
            )
            if cur.rowcount == 0:
                raise KeyError(f"Assessment ID {assessment_id} not found")
            self._conn.commit()
            row = self._conn.execute(
                "SELECT * FROM assessments WHERE id = ?",
                (assessment_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"Assessment ID {assessment_id} not found")
        return {
            "id": row["id"],
            "module_code": row["module_code"],
            "title": row["title"],
            "weight_pct": row["weight_pct"],
            "due_date": row["due_date"],
            "completed": bool(row["completed"]),
        }

    def delete_assessment(self, assessment_id: int) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM assessments WHERE id = ?", (assessment_id,))
            self._conn.commit()
            return cur.rowcount > 0

    def delete_revision_topic(self, topic_id: int) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM revision_topics WHERE id = ?", (topic_id,))
            self._conn.commit()
            return cur.rowcount > 0

    def add_revision_topic(self, module_code: str, topic: str) -> dict[str, Any]:
        code = module_code.strip().upper()
        cleaned_topic = topic.strip()
        if not cleaned_topic:
            raise ValueError("Topic must not be blank")
        today = date.today().isoformat()
        with self._lock:
            mod = self._conn.execute(
                "SELECT code FROM modules WHERE code = ?", (code,)
            ).fetchone()
            if mod is None:
                raise KeyError(f"Module '{code}' does not exist")

            cur = self._conn.execute(
                """
                INSERT INTO revision_topics (
                    module_code, topic, repetitions, easiness, interval_days, next_review_date
                ) VALUES (?, ?, 0, 2.5, 1, ?)
                """,
                (code, cleaned_topic, today),
            )
            self._conn.commit()
            topic_id = int(cur.lastrowid or 0)
        return {
            "id": topic_id,
            "module_code": code,
            "topic": cleaned_topic,
            "repetitions": 0,
            "easiness": 2.5,
            "interval_days": 1,
            "next_review_date": today,
        }

    def list_revision_topics(
        self, module_code: str | None = None, due_only: bool = False
    ) -> list[dict[str, Any]]:
        today = date.today().isoformat()
        clauses: list[str] = []
        params: list[Any] = []
        if module_code:
            clauses.append("module_code = ?")
            params.append(module_code.strip().upper())
        if due_only:
            clauses.append("next_review_date <= ?")
            params.append(today)
        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM revision_topics {where_sql} ORDER BY next_review_date ASC, id ASC",
                params,
            ).fetchall()
        return [dict(r) for r in rows]

    def record_topic_review(self, topic_id: int, quality: int) -> dict[str, Any]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM revision_topics WHERE id = ?", (topic_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"Topic ID {topic_id} not found")

            reps, ef, interval = compute_sm2_schedule(
                quality=quality,
                repetitions=int(row["repetitions"]),
                easiness=float(row["easiness"]),
                interval_days=int(row["interval_days"]),
            )
            next_date = (date.today() + timedelta(days=interval)).isoformat()
            self._conn.execute(
                """
                UPDATE revision_topics
                SET repetitions = ?, easiness = ?, interval_days = ?, next_review_date = ?
                WHERE id = ?
                """,
                (reps, ef, interval, next_date, topic_id),
            )
            self._conn.commit()
        return {
            "id": topic_id,
            "module_code": row["module_code"],
            "topic": row["topic"],
            "repetitions": reps,
            "easiness": ef,
            "interval_days": interval,
            "next_review_date": next_date,
        }

    def get_overview(self) -> dict[str, Any]:
        today = date.today().isoformat()
        with self._lock:
            modules = self._conn.execute("SELECT * FROM modules ORDER BY code ASC").fetchall()
            assessments = self._conn.execute("SELECT * FROM assessments").fetchall()
            topic_row = self._conn.execute(
                """
                SELECT
                    COUNT(*) AS total_topics,
                    COALESCE(SUM(CASE WHEN next_review_date <= ? THEN 1 ELSE 0 END), 0) AS due_today
                FROM revision_topics
                """,
                (today,),
            ).fetchone()

        total_ass = len(assessments)
        completed_ass = sum(1 for a in assessments if bool(a["completed"]))
        overdue_ass = sum(
            1 for a in assessments if not bool(a["completed"]) and str(a["due_date"]) < today
        )
        return {
            "module_count": len(modules),
            "total_credits": sum(int(m["credits"]) for m in modules),
            "assessments": {
                "total": total_ass,
                "completed": completed_ass,
                "pending": total_ass - completed_ass,
                "overdue": overdue_ass,
            },
            "topics": {
                "total": int(topic_row["total_topics"]) if topic_row else 0,
                "due_today": int(topic_row["due_today"]) if topic_row else 0,
            },
        }


def create_app(db: PlannerDatabase | None = None, db_path: str | None = None) -> FastAPI:
    owns_db = db is None
    resolved_db_path = db_path or os.environ.get("STUDY_PLANNER_DB_PATH", "sm2_study_planner.db")
    state: dict[str, PlannerDatabase | None] = {"db": db}

    def get_db() -> PlannerDatabase:
        current = state["db"]
        if current is None:
            current = PlannerDatabase(resolved_db_path)
            state["db"] = current
        return current

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            if owns_db and state["db"] is not None:
                state["db"].close()
                state["db"] = None

    app = FastAPI(
        title="SM-2 Study Planner",
        version="1.3.0",
        lifespan=lifespan,
    )

    @app.get("/", response_class=HTMLResponse)
    def index_ui() -> str:
        return """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>SM-2 Study Planner · Modules &amp; Spaced Repetition</title>
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <style>
    body { background: #0a0c10; color: #eceff4; font-family: system-ui, sans-serif; margin: 0; padding: 2rem; }
    .container { max-width: 1080px; margin: 0 auto; }
    h1 { font-size: 1.9rem; margin-bottom: 0.25rem; }
    .sub { color: #8b96a9; font-family: monospace; font-size: 0.85rem; margin-bottom: 1.5rem; }
    .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 1.25rem; }
    .card { background: #101319; border: 1px solid #1d222c; padding: 1.25rem; border-radius: 4px; }
    label { display: block; font-size: 0.8rem; color: #8b96a9; margin-top: 0.7rem; }
    input, select, button { width: 100%; padding: 0.55rem 0.7rem; margin-top: 0.3rem; background: #0a0c10; border: 1px solid #262e3d; color: #eceff4; box-sizing: border-box; border-radius: 3px; }
    button { background: #58c48b; color: #0a0c10; font-weight: 600; cursor: pointer; margin-top: 0.9rem; border: none; }
    .status { min-height: 1.25rem; margin-top: 0.6rem; font-size: 0.82rem; color: #f87171; }
    .ok { color: #58c48b; }
    pre { overflow-x: auto; font-size: 0.82rem; color: #cad2df; margin: 0; }
  </style>
</head>
<body>
  <div class="container">
    <h1>SM-2 Study Planner</h1>
    <p class="sub">FastAPI · SQLite · SuperMemo-2 Spaced Repetition · OpenAPI Docs (/docs)</p>
    <div class="grid">
      <div class="card">
        <h2>Register Module</h2>
        <label for="code">Module Code</label><input id="code" value="CM2104" />
        <label for="title">Module Title</label><input id="title" value="Computational Mathematics" />
        <label for="credits">Credits</label><input id="credits" type="number" value="20" />
        <label for="semester">Semester</label>
        <select id="semester">
          <option value="Autumn">Autumn</option>
          <option value="Spring">Spring</option>
          <option value="Full Year">Full Year</option>
        </select>
        <button id="save-btn">Save Module</button>
        <h2 style="margin-top:1.4rem;">Add Revision Topic</h2>
        <label for="topic-mod">Module Code</label><input id="topic-mod" value="CM2104" />
        <label for="topic-text">Topic</label><input id="topic-text" value="Eigenvalues and diagonalisation" />
        <button id="topic-btn">Add Topic</button>
        <p id="form-status" class="status"></p>
      </div>
      <div class="card">
        <h2>Modules &amp; Due Topics Queue</h2>
        <pre id="modules-out">Loading...</pre>
      </div>
    </div>
  </div>
  <script>
    const statusEl = document.getElementById('form-status');
    function showStatus(msg, ok) {
      statusEl.textContent = msg;
      statusEl.className = ok ? 'status ok' : 'status';
    }
    async function refresh() {
      const [modsRes, dueRes] = await Promise.all([fetch('/api/modules'), fetch('/api/topics/due')]);
      const data = { modules: await modsRes.json(), due_topics: await dueRes.json() };
      document.getElementById('modules-out').textContent = JSON.stringify(data, null, 2);
    }
    document.getElementById('save-btn').addEventListener('click', async () => {
      const res = await fetch('/api/modules', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          code: document.getElementById('code').value,
          title: document.getElementById('title').value,
          credits: Number(document.getElementById('credits').value),
          semester: document.getElementById('semester').value
        })
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        showStatus(err.detail || 'Could not save module', false);
        return;
      }
      showStatus('Module saved.', true);
      refresh();
    });
    document.getElementById('topic-btn').addEventListener('click', async () => {
      const res = await fetch('/api/topics', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          module_code: document.getElementById('topic-mod').value,
          topic: document.getElementById('topic-text').value
        })
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        showStatus(err.detail || 'Could not add topic', false);
        return;
      }
      showStatus('Revision topic scheduled for review.', true);
      refresh();
    });
    refresh();
  </script>
</body>
</html>"""

    @app.get("/api/overview")
    def get_overview() -> dict[str, Any]:
        return get_db().get_overview()

    @app.post("/api/modules", status_code=201)
    def create_module(payload: ModuleCreate) -> dict[str, Any]:
        try:
            return get_db().create_module(payload)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/modules")
    def list_modules() -> list[dict[str, Any]]:
        return get_db().list_modules()

    @app.delete("/api/modules/{code}", status_code=204)
    def delete_module(code: str) -> Response:
        if not get_db().delete_module(code):
            raise HTTPException(status_code=404, detail=f"Module '{code}' not found")
        return Response(status_code=204)

    @app.post("/api/assessments", status_code=201)
    def create_assessment(payload: AssessmentCreate) -> dict[str, Any]:
        try:
            return get_db().create_assessment(payload)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/assessments")
    def list_assessments(
        module_code: str | None = None,
        pending_only: bool = False,
    ) -> list[dict[str, Any]]:
        return get_db().list_assessments(module_code, pending_only=pending_only)

    @app.patch("/api/assessments/{assessment_id}/complete")
    def set_assessment_completed(
        assessment_id: int,
        payload: AssessmentStatusUpdate,
    ) -> dict[str, Any]:
        try:
            return get_db().set_assessment_completed(assessment_id, payload.completed)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.delete("/api/assessments/{assessment_id}", status_code=204)
    def delete_assessment(assessment_id: int) -> Response:
        if not get_db().delete_assessment(assessment_id):
            raise HTTPException(status_code=404, detail=f"Assessment ID {assessment_id} not found")
        return Response(status_code=204)

    @app.post("/api/topics", status_code=201)
    def create_topic(payload: TopicCreate) -> dict[str, Any]:
        try:
            return get_db().add_revision_topic(payload.module_code, payload.topic)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.delete("/api/topics/{topic_id}", status_code=204)
    def delete_topic(topic_id: int) -> Response:
        if not get_db().delete_revision_topic(topic_id):
            raise HTTPException(status_code=404, detail=f"Topic ID {topic_id} not found")
        return Response(status_code=204)

    @app.get("/api/topics")
    def list_topics(module_code: str | None = None) -> list[dict[str, Any]]:
        return get_db().list_revision_topics(module_code=module_code)

    @app.get("/api/topics/due")
    def list_due_topics(module_code: str | None = None) -> list[dict[str, Any]]:
        return get_db().list_revision_topics(module_code=module_code, due_only=True)

    @app.post("/api/topics/{topic_id}/review")
    def review_topic(topic_id: int, payload: ReviewUpdate) -> dict[str, Any]:
        try:
            return get_db().record_topic_review(topic_id, payload.quality)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    return app


app = create_app()


def main(argv: Sequence[str] | None = None) -> int:
    import uvicorn

    parser = argparse.ArgumentParser(description="Start the SM-2 Study Planner API server.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)
    uvicorn.run("study_planner.app:app", host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
