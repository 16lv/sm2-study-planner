# sm2-study-planner

Module tracker, coursework deadline manager, and SuperMemo-2 (SM-2) spaced-repetition planner built with **FastAPI**, **Pydantic v2**, and **SQLite**.

## Overview

- **SM-2 scheduling engine**: `compute_sm2_schedule()` calculates repetition streaks, easiness factors (clamped at `>= 1.3`), and review gaps (`1d`, `6d`, then `interval * EF`), resetting low ratings (`< 3`) back to `1d`.
- **Safe relational storage**: Uses foreign keys with `ON DELETE CASCADE`, `ON CONFLICT DO UPDATE` upserts when updating modules so coursework and study cards stay intact, and lazy SQLite initialisation configurable via `STUDY_PLANNER_DB_PATH`.
- **Thread-safe persistence**: `PlannerDatabase` protects concurrent requests with `threading.RLock` and WAL mode when running on disk.

## Endpoints

| Method | Path | Description |
| :--- | :--- | :--- |
| `GET` | `/` | Minimal web workspace |
| `POST` / `GET` | `/api/modules` | Register/update or list modules |
| `DELETE` | `/api/modules/{code}` | Remove a module and cascading assessments/topics |
| `POST` / `GET` | `/api/assessments` | Log coursework deadlines or filter by module |
| `PATCH` | `/api/assessments/{id}/complete` | Toggle coursework completion state |
| `POST` / `GET` | `/api/topics` | Add or list revision flashcards |
| `GET` | `/api/topics/due` | Return topics scheduled for review today |
| `POST` | `/api/topics/{topic_id}/review` | Submit recall quality (`0..5`) and reschedule |

## Development

```bash
pip install -e ".[dev]"
ruff check .
pytest -v
uvicorn src.study_planner.app:app --reload
```
