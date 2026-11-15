# ClipForge AI — working notes for Claude

Read this first, then [docs/PROJECT_STATUS.md](docs/PROJECT_STATUS.md) for
where the work stands.

## What this is

A local web app that turns one long video into reviewed, ready-to-publish
YouTube Shorts. Python (FastAPI, SQLite, an in-process job queue) serves a
React dashboard; one command starts it. Built in ten phases, each ending with
a test on real output.

## The documents

| File | Holds | Update when |
| --- | --- | --- |
| [docs/PROJECT_STATUS.md](docs/PROJECT_STATUS.md) | One page: what is done, open, next | End of every phase |
| [docs/ROADMAP.md](docs/ROADMAP.md) | The ten phases, each phase's gate and its recorded results (this is the implementation roadmap) | End of every phase |
| [docs/TASKS.md](docs/TASKS.md) | Feature requests broken into items marked built / partly built / not built | When an item changes state |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Design, and an "as built" note per phase | When a phase changes the design |
| [README.md](README.md) | What the app does today, how to run and set it up | When what the user sees changes |

At the end of a phase, all five are brought up to date before the phase is
reported as done. Record what was tested and how, what failed and was fixed,
and what was not done. Never call a feature done because its screen exists.

## Layout

| Path | What |
| --- | --- |
| `clipforge.cmd` | Launcher: starts the server and opens the dashboard (port 8765) |
| `backend/clipforge/app.py` | App factory, routes, WebSocket progress |
| `backend/clipforge/jobs/engine.py` | SQLite-backed job queue; stages save results so jobs resume after a crash |
| `backend/clipforge/pipelines.py` | The pipelines and every stage function |
| `backend/clipforge/ingest/` | Video sources (yt-dlp based) |
| `backend/clipforge/analysis/` | Sound and picture signals, speech-to-text |
| `backend/clipforge/ai/` | AI provider layer (`Reasoner.ask_json`) |
| `backend/clipforge/moments.py` | AI moment detection and viral score |
| `backend/clipforge/duration.py` | Smart Duration engine: exact clip boundaries and part splitting, no AI |
| `backend/clipforge/captions.py` | Word-synced captions: lines, styles, ASS output, timing and placement checks |
| `backend/clipforge/rights.py` | Rights record and the download gate |
| `frontend/src/` | React + TypeScript + Tailwind dashboard |
| `data/` | Database, project files, speech models (not in git) |
| `tools/ffmpeg/` | FFmpeg and ffprobe (not in git) |
| `tools/fonts/` | Caption fonts, SIL Open Font License (not in git; sources in README) |

## Commands

Run from the repository root (`D:\ClipForge-AI`).

- Tests: `backend\.venv\Scripts\python -m pytest -q` from `backend`
  (about 250 tests, two to four minutes).
- Dashboard build, after any frontend change: `npm run build` in `frontend`.
  The server serves `frontend/dist`; it does not hot-reload.
- Start the app: `clipforge.cmd` (`--port`, `--no-browser`, `--data-dir`).

## Rules that must hold

- **Rights.** A link is downloaded only with a rights record (own, or
  licensed / creator permission with creator and evidence). The check is in
  `rights.py` and is repeated inside the download stages. Credit is never
  treated as permission. Never sign in, use cookies, or work around platform
  access limits.
- **Tests have no real side effects.** `tests/conftest.py` replaces the
  platforms, the speech model, the AI and the file dialog with stand-ins.
  New outward-facing code needs a stand-in there too.
- **Cutting and captioning never call the AI.** Changing duration settings
  re-cuts from stored moments; changing the caption style re-captions without
  re-cutting; only "Pick moments again" asks the AI.
- **Captions show what was heard.** Never correct, add or drop a word; flag
  unsure ones.
- **Database changes** must be new nullable columns: `db.add_missing_columns`
  upgrades existing databases in place.
- **Heavy files live on D:.** C: is nearly full.
- **Changes are kept small.** Extend the existing modules; do not restructure
  working code without a reason found in testing.

## Checking work in a browser

- Use the preview configuration `clipforge-test`: port 8799, data in
  `D:\tmp\cf_dev_data`, never the real `data\`.
- To reuse the downloaded speech models there, make a directory junction
  `D:\tmp\cf_dev_data\cache\models` → `D:\ClipForge-AI\data\cache\models`.
  Remove the junction with `cmd /c rmdir` before deleting the test folder,
  or the real models are deleted with it.
- Stopping the preview can leave the Python process running. Afterwards,
  stop whatever still listens on ports 8799 and 8800, or a stale server with
  old code will share the test database.
- Test videos must be ones the owner may use, or openly licensed. Tears of
  Steel (CC BY 3.0, Blender Foundation) has been used for real speech.
- Scratch scripts go outside the repository and must not share a name with a
  Python standard module (a file called `inspect.py` broke `httpx`).
- A job keeps the stage list it was created with. After adding a stage,
  restart the test server and start a new job; a job queued under the old
  code resumes without the new stage.
- When checking rendered frames at a given time, sample well inside the
  window being tested: the frame shown at time t is the first one at or after
  t, so a time within one frame of a change shows what comes next.

## Releasing a phase

The owner pushes to GitHub themselves. At the end of each phase, give them
three commands (`git -C D:\ClipForge-AI add -A`, `commit -m "Phase N: …"`,
`push`) after checking that nothing private, no media and nothing from
`data/`, `tools/`, `.venv/`, `node_modules/` or `.claude/` would be included.
The repository is public. Do not commit or push for them, and do not add a
co-author line to commit messages.
