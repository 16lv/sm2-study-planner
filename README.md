# sm2-study-planner

Course module tracker, coursework deadline manager, and SuperMemo-2 (SM-2) spaced-repetition planner built with **Python 3.12**, **FastAPI**, **Pydantic v2**, and **SQLite**.

## Overview

`sm2-study-planner` stores university modules, weighted coursework assessments, and revision topics in SQLite with foreign-key cascading rules, and schedules flashcard reviews using the SM-2 repetition algorithm.

## Features

- **SM-2 scheduling engine**: `compute_sm2_schedule()` calculates repetition streaks, easiness factors (clamped at `>= 1.3`), and review gaps (`1d`, `6d`, then rounded `interval * EF`), resetting low recall scores (`< 3`) back to `1d`.
- **Safe relational persistence**: Uses `ON DELETE CASCADE` foreign keys, `ON CONFLICT DO UPDATE` upserts so renaming module titles keeps child assessments and flashcards intact, and lazy SQLite initialisation configurable via `STUDY_PLANNER_DB_PATH`.
- **Thread-safe database access**: `PlannerDatabase` coordinates concurrent requests with `threading.RLock`, `WAL` journal mode on disk, and context-manager lifecycle support.
- **Built-in web workspace**: `/` serves a dependency-free HTML form UI for saving modules, adding revision topics, and viewing today's due review queue.

## Technology Stack

- Python 3.11+ (tested on Python 3.12)
- FastAPI & Uvicorn
- Pydantic v2
- SQLite3 (`sqlite3` standard library)
- pytest, HTTPX TestClient, Ruff

## Installation

```bash
pip install -e ".[dev]"
```

## Endpoints

| Method | Path | Description |
| :--- | :--- | :--- |
| `GET` | `/` | Embedded single-page web workspace |
| `POST` / `GET` | `/api/modules` | Register/update or list modules |
| `DELETE` | `/api/modules/{code}` | Remove a module and cascading assessments/topics (`204`) |
| `POST` / `GET` | `/api/assessments` | Log coursework deadlines or filter by `?module_code=` |
| `PATCH` | `/api/assessments/{id}/complete` | Toggle coursework completion state |
| `DELETE` | `/api/assessments/{id}` | Remove an assessment (`204`) |
| `POST` / `GET` | `/api/topics` | Add (JSON body) or list revision flashcards |
| `DELETE` | `/api/topics/{id}` | Remove a revision flashcard (`204`) |
| `GET` | `/api/topics/due` | Return topics scheduled for review today |
| `POST` | `/api/topics/{topic_id}/review` | Submit recall quality (`0..5`) and reschedule |

## Usage Examples

Start the server using the installed script or `uvicorn`:

```bash
study-planner --host 127.0.0.1 --port 8000
# or:
uvicorn study_planner.app:app --reload
```

Create a module and schedule a topic via `curl`:

```bash
curl -X POST http://127.0.0.1:8000/api/modules \
  -H "Content-Type: application/json" \
  -d '{"code":"CM2102","title":"Database Systems","credits":20,"semester":"Autumn"}'

curl -X POST http://127.0.0.1:8000/api/topics \
  -H "Content-Type: application/json" \
  -d '{"module_code":"CM2102","topic":"BCNF dependency preservation"}'
```

## Technical Implementation Details

- Upserting an existing module uses `ON CONFLICT(code) DO UPDATE` rather than `INSERT OR REPLACE`, avoiding accidental trigger/cascade deletions on child rows.
- `create_app()` defers opening `PlannerDatabase` until the first request hits a handler, allowing the module to be imported in tooling and tests without writing `sm2_study_planner.db` to disk.

## Project Structure

```text
sm2-study-planner/
├── src/study_planner/
│   ├── __init__.py
│   └── app.py
├── tests/
│   └── test_planner.py
├── pyproject.toml
└── requirements.txt
```

## Testing

```bash
ruff check .
pytest -v
```

## Known Limitations & Future Improvements

- Single-user local deployment with no authentication or multi-user workspaces.
- The embedded HTML workspace exposes module and topic creation; exposing assessment toggling and inline review buttons directly in the UI alongside iCalendar (`.ics`) deadline export is planned.
