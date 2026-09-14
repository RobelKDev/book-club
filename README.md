# The Reading Room — *Legimus ergo sumus*

A private, two-person book club web app for **Robel** (New York) and **Bitania** (Addis Ababa).

They take turns recommending books, read **chapter by chapter in lockstep**, keep private notes, and each write an end-of-book essay — then discuss. The app enforces the lockstep, guards against spoilers, and is designed phone-first in a Classics / Academic / Poetic style (Cormorant Garamond + EB Garamond, parchment & ink, oxblood & antique gold, Roman numerals, ❦ ornaments, drop caps).

## How the club works

1. **Recommend** — members alternate recommending books (the app tells you whose turn it is).
2. **Read in lockstep** — the chapter-gating rule (below) means nobody reads ahead alone.
3. **Take notes** — private per-chapter marginalia and a private reading journal, autosaved.
4. **Chew into it** — when you finish the last chapter, write your end-of-book essay.
5. **Library** — once *both* essays are sealed, the book moves to the library with both essays revealed side by side.

## The chapter-gating rule (the heart of the app)

> A member may read chapter *k* only if **both** members have completed chapter *k−1*.

Concretely, each reader's current chapter is `min(both members' completed chapters) + 1`. Completing a chapter is one tap: **"Mark Chapter N complete"**. The button only ever appears on the single open chapter, and only for the reader whose turn it is — if you've read ahead of your partner, the chapter waits on them by name, with a poetic holding message. Locked chapters show who must finish what before they open.

## Spoiler safety

- Notes are private per member — your partner can never see them.
- An essay is visible to the other person only **after both essays are submitted**. Until then, sealed essays show only a holding message.
- A book's status flips to `completed` (and it enters the library) only when both essays are in.

## Identity model

There are exactly two members, seeded as **Robel** and **Bitania**. The first screen asks *"Who is reading?"* — the choice is remembered in a long-lived signed cookie. **There are no passwords** by design: this is a private club of two, not a public service. Don't expose it to the open internet without adding authentication in front of it (or keep the URL unlisted and private).

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

Application code keeps writing SQLite-style SQL with `?` placeholders; `db.py` translates them to `%s` for Postgres, returns dict-like rows, and handles the small portability gaps (`SERIAL` vs `AUTOINCREMENT`, `RETURNING id` vs `lastrowid`, `ON CONFLICT DO NOTHING` vs `INSERT OR IGNORE`). Behavior — routes, gating, spoiler rules — is identical on both.

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
- `books` — `id`, `title`, `author`, `chapters_total`, `recommended_by`, `status` (`reading`/`completed`), `created_at`
- `progress` — `member_id`, `book_id`, `chapters_completed`
- `chapter_notes` — `member_id`, `book_id`, `chapter`, `text`, `updated_at` (private per member)
- `book_notes` — `member_id`, `book_id`, `text`, `updated_at` (private per member)
- `essays` — `member_id`, `book_id`, `text`, `submitted_at`

## Routes

| Route | What it does |
|---|---|
| `GET /` | Dashboard (redirects to `/identity` if unknown) |
| `GET/POST /identity` | "Who is reading?" gate / switch reader |
| `GET/POST /book/add` | Shelve a new book |
| `GET /book/<id>` | Book detail: chapters, notes, essay |
| `POST /book/<id>/complete` | Mark the open chapter complete (gating enforced) |
| `POST /book/<id>/chapter-note` | Autosave a chapter note (JSON) |
| `POST /book/<id>/book-note` | Autosave the reading journal (JSON) |
| `POST /book/<id>/essay` | Save essay draft or seal the essay |
| `GET /library` | Finished volumes with both essays side by side |

## Project structure

```
book-club/
├── app.py               # Flask app: routes, gating logic
├── db.py                # Database layer: SQLite by default, Postgres when DATABASE_URL is set
├── requirements.txt
├── Procfile             # gunicorn for Render/Railway/Heroku-style hosts
├── render.yaml          # one-click Render blueprint (free tier + Postgres)
├── Dockerfile
├── .gitignore
├── README.md
├── templates/           # base, identity, dashboard, book, add_book, library
└── static/style.css     # the parchment & ink theme
```
