# The Reading Room — *Legimus ergo sumus*

A private, two-person book club web app for **Robel** (New York) and **Bitania** (Addis Ababa).

They take turns recommending books, read each book cover to cover, and each write an end-of-book essay — then discuss. The app guards against spoilers and checkpoints every word of each essay draft to the backend as it is written, and is designed phone-first in a Classics / Academic / Poetic style (Cormorant Garamond + EB Garamond, parchment & ink, oxblood & antique gold, Roman numerals, ❦ ornaments, drop caps).

## How the club works

1. **Recommend** — members alternate recommending books (a slim ribbon on the dashboard tells you whose turn it is).
2. **Read** — the whole book, at your own pace. No chapters, no lockstep, no journal.
3. **Chew into it** — when you finish, write your end-of-book essay. Your draft is checkpointed to the backend on every write (plus a last-chance save when the page closes), so nothing is ever lost.
4. **Shelf** — once *both* essays are sealed, the book moves to the shelf with both essays revealed side by side.

## Essay states

Each reader's essay moves through three states, tracked per `(member, book)`:

- **Draft** — `submitted_at` is NULL; the text is checkpointed to the backend on every keystroke pause and again when the page is hidden or closed. `updated_at` records the last checkpoint.
- **Sealed** — the reader tapped "Seal the essay"; `submitted_at` is set. The text is hidden from the partner, and further draft saves are ignored (a sealed essay can't be overwritten by a draft checkpoint).
- **Revealed** — both readers have sealed; each can read the other's essay, and the book's status flips to `completed`.

## Spoiler safety

- An essay is visible to the other person only **after both essays are submitted**. Until then, sealed essays show only a holding message.
- A book's status flips to `completed` (and it enters the shelf) only when both essays are in.

## Identity model

There are exactly two members, seeded as **Robel** and **Bitania**. The first screen asks *"Who is reading?"* — pick your name, enter your private passcode (set via the `PASSCODE_ROBEL` / `PASSCODE_BITANIA` environment variables, never committed), and the choice is remembered in a long-lived signed cookie. Keep the URL unlisted and private all the same.

## Tech

- **Python Flask** + **SQLite** by default, **Postgres** when `DATABASE_URL` is set (see below) — no ORM, no external services otherwise
- Server-rendered pages + a little vanilla JS (autosave, dual clocks)
- No build step, no JavaScript framework

## Database backends

The app picks its database at boot (see `db.py`):

| `DATABASE_URL` set? | Backend | Used when |
|---|---|---|
| No | **SQLite** at `DATABASE_PATH` (default `./data/bookclub.db`) | Local development — zero setup |
| Yes | **Postgres** via `psycopg2` | Production on Render's free tier |

Application code keeps writing SQLite-style SQL with `?` placeholders; `db.py` translates them to `%s` for Postgres, returns dict-like rows, and handles the small portability gaps (`SERIAL` vs `AUTOINCREMENT`, `RETURNING id` vs `lastrowid`, `ON CONFLICT DO NOTHING` vs `INSERT OR IGNORE`). Behavior — routes, essay states, spoiler rules — is identical on both.

**Why Postgres on Render's free tier:** Render's filesystem is ephemeral, so a SQLite file would be wiped on every redeploy or restart. (Render's persistent disks require a paid instance type.) The free Postgres instance keeps the club's data safe across deploys — just paste its Internal Database URL into the `DATABASE_URL` env var at deploy time; it is never committed to the repo (`sync: false` in `render.yaml`).

## Run locally

```bash
cd book-club
pip install -r requirements.txt
python app.py
```

Then open http://localhost:5000 — choose Robel or Bitania, shelve a book, and read.

Environment variables:

| Variable        | Default                    | Purpose                                  |
|-----------------|----------------------------|------------------------------------------|
| `PORT`          | `5000`                     | Port to listen on                        |
| `DATABASE_PATH` | `./data/bookclub.db`       | Where the SQLite file lives (ignored when `DATABASE_URL` is set) |
| `DATABASE_URL`  | *(unset)*                  | Postgres connection string — when set, the app uses Postgres instead of SQLite |
| `SECRET_KEY`    | `dev-secret-key-change-me` | Signs the identity cookie — **set this in production** |

## Deploy to Render (one-click, free tier)

1. Push this folder to a GitHub repo.
2. In the Render dashboard: **New → Postgres**, create a free database.
3. **New → Blueprint**, point it at the repo. `render.yaml` provisions the web service automatically; `SECRET_KEY` is generated for you.
4. When prompted for `DATABASE_URL`, paste the Postgres instance's **Internal Database URL**. The app will use Postgres from then on, so your books, notes, and essays survive redeploys and restarts on the free plan.

## Deploy to Railway

1. Push this folder to a GitHub repo.
2. In Railway: **New Project → Deploy from GitHub**, select the repo. Railway detects the `Procfile` and runs `gunicorn app:app`.
3. Add a variable `SECRET_KEY` (generate a random string) under **Variables**.
4. For data that survives restarts, add a **Volume** mounted at `/var/data` and set `DATABASE_PATH=/var/data/bookclub.db` in Variables. (Without a volume, the SQLite file is ephemeral.)
5. Railway assigns a public domain automatically under **Settings → Networking**.

Alternatively, build the included `Dockerfile` anywhere that runs containers:

```bash
docker build -t reading-room .
docker run -p 8000:8000 -e SECRET_KEY=your-secret -v bookclub-data:/srv/data reading-room
```

## Data model

- `members` — `id`, `name` (seeded: Robel, Bitania)
- `books` — `id`, `title`, `author`, `chapters_total` (vestigial, always 1 for new books), `recommended_by`, `status` (`reading`/`completed`), `created_at`, `cover_url`
- `essays` — `member_id`, `book_id`, `text`, `submitted_at` (NULL = draft), `updated_at` (last draft checkpoint)

The old `progress`, `chapter_notes`, and `book_notes` tables are dropped by a migration at boot.

## Routes

| Route | What it does |
|---|---|
| `GET /` | Dashboard (redirects to `/identity` if unknown) |
| `GET/POST /identity` | "Who is reading?" gate / switch reader |
| `GET/POST /book/add` | Shelve a new book |
| `GET /book/<id>` | Book detail: the one ending essay |
| `POST /book/<id>/essay` | Checkpoint an essay draft (`action=save`) or seal the essay (`action=submit`) |
| `GET /shelf` | Finished volumes with both essays side by side |
| `GET /library` | Redirects to `/shelf` (old name kept working) |

## Project structure

```
book-club/
├── app.py               # Flask app: routes, essay state machine
├── db.py                # Database layer: SQLite by default, Postgres when DATABASE_URL is set
├── requirements.txt
├── Procfile             # gunicorn for Render/Railway/Heroku-style hosts
├── render.yaml          # one-click Render blueprint (free tier + Postgres)
├── Dockerfile
├── .gitignore
├── README.md
├── templates/           # base, identity, dashboard, book, add_book, shelf
└── static/style.css     # the parchment & ink theme
```
