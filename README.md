# sm2-study-planner

University module tracker, assessment deadline log, and SuperMemo-2 (SM-2) spaced-repetition planner built with **FastAPI**, **Pydantic v2**, and **SQLite**.

## Overview

- **SM-2 scheduling engine**: `compute_sm2_schedule()` calculates repetition count, easiness factor (`>= 1.3`), and next review interval (`1d`, `6d`, then `interval * EF`), resetting to `1d` when recall quality is below `3`.
- **Relational schema**: Foreign keys (`PRAGMA foreign_keys = ON`) link `assessments` and `revision_topics` to `modules`. Module updates use `ON CONFLICT(code) DO UPDATE` so child assessments and topics are preserved.
- **Thread-safe persistence**: `PlannerDatabase` serialises concurrent FastAPI worker access via `threading.RLock` with WAL mode on file-backed databases.

## Structure

```text
sm2-study-planner/
├── src/study_planner/
│   └── app.py          # SM-2 engine, PlannerDatabase, FastAPI routes, web workspace
├── tests/
│   └── test_planner.py
├── pyproject.toml
└── requirements.txt
```

## Development

```bash
pip install -e ".[dev]"
ruff check .
pytest -v
uvicorn src.study_planner.app:app --reload
```
