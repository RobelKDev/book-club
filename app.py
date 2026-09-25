#!/usr/bin/env python3
"""
The Reading Room -- a private two-person book club.

For Robel (New York) and Bitania (Addis Ababa).

The club reads whole books: each reader writes one end-of-book essay.
Essays are spoiler-safe -- a reader's essay is revealed only once both
readers have sealed theirs, at which point the book moves to the shelf.
Essay drafts autosave to the backend as they are written.
"""

import hmac
import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from flask import (
    Flask, abort, flash, redirect, render_template,
    request, session, url_for,
)

from db import close_db, get_db, init_db, insert_returning_id

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-key-change-me")
app.permanent_session_lifetime = timedelta(days=365 * 5)


# --------------------------------------------------------------------------- #
# Database helpers (see db.py: SQLite by default, Postgres when DATABASE_URL
# is set)
# --------------------------------------------------------------------------- #

@app.teardown_appcontext
def _close_db(exc):
    close_db(exc)


# --------------------------------------------------------------------------- #
# Small utilities
# --------------------------------------------------------------------------- #

_ROMAN = [
    (1000, "M"), (900, "CM"), (500, "D"), (400, "CD"),
    (100, "C"), (90, "XC"), (50, "L"), (40, "XL"),
    (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I"),
]


def to_roman(n):
    if n <= 0:
        return str(n)
    parts = []
    for value, numeral in _ROMAN:
        while n >= value:
            parts.append(numeral)
            n -= value
    return "".join(parts)


app.jinja_env.filters["roman"] = to_roman


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def current_member():
    # A session only counts as logged in once the passcode gate has been
    # passed (session["authed"]). Cookies minted before the passcode gate
    # existed carry member_id without authed and are treated as logged out.
    if not session.get("authed"):
        return None
    mid = session.get("member_id")
    if not mid:
        return None
    return get_db().execute("SELECT * FROM members WHERE id = ?", (mid,)).fetchone()


def passcode_for(member):
    """The expected passcode for a member, from the environment.

    PASSCODE_<NAME> (e.g. PASSCODE_ROBEL). Returns None when unset, in
    which case login is denied (fail closed).
    """
    expected = os.environ.get(f"PASSCODE_{member['name'].upper()}")
    return expected if expected else None


def passcode_ok(member, given):
    expected = passcode_for(member)
    if not expected or not given:
        return False
    return hmac.compare_digest(given, expected)


def members_list():
    return get_db().execute("SELECT * FROM members ORDER BY id").fetchall()


def other_member(me, members=None):
    members = members if members is not None else members_list()
    for m in members:
        if m["id"] != me["id"]:
            return m
    return None


def essays_map(book_id):
    rows = get_db().execute(
        "SELECT * FROM essays WHERE book_id = ?", (book_id,)
    ).fetchall()
    return {r["member_id"]: r for r in rows}


def get_book_or_404(book_id):
    book = get_db().execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
    if book is None:
        abort(404)
    return book


_OL_SEARCH_URL = "https://openlibrary.org/search.json"


def search_books_online(query, limit=6):
    """Look up books on Open Library.

    Returns a list of dicts (title, author, year, cover_url, thumb_url),
    an empty list when nothing matches, or None when the lookup itself
    failed (network down, service error) so callers can fall back to
    manual entry.
    """
    query = (query or "").strip()
    if not query:
        return []
    try:
        params = urllib.parse.urlencode({
            "q": query,
            "limit": limit,
            "fields": "key,title,author_name,first_publish_year,cover_i",
        })
        req = urllib.request.Request(
            _OL_SEARCH_URL + "?" + params,
            headers={"User-Agent": "ReadingRoomBookClub/1.0 (two-person book club)"},
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.load(resp)
    except Exception:  # noqa: BLE001 - any lookup failure -> manual fallback
        return None
    results = []
    for doc in data.get("docs", [])[:limit]:
        cover_i = doc.get("cover_i")
        authors = doc.get("author_name") or []
        results.append({
            "title": doc.get("title") or "Untitled",
            "author": ", ".join(authors[:3]),
            "year": doc.get("first_publish_year"),
            "cover_url": (
                f"https://covers.openlibrary.org/b/id/{cover_i}-L.jpg"
                if cover_i else ""
            ),
            "thumb_url": (
                f"https://covers.openlibrary.org/b/id/{cover_i}-M.jpg"
                if cover_i else ""
            ),
        })
    return results


def clean_cover_url(value):
    """Keep only sane http(s) cover URLs; drop anything else."""
    value = (value or "").strip()
    if len(value) > 500:
        return ""
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return ""
    return value


def next_recommender():
    """Whose turn it is to recommend: whoever did NOT recommend the latest book."""
    db = get_db()
    members = members_list()
    last = db.execute(
        "SELECT recommended_by FROM books ORDER BY created_at DESC, id DESC LIMIT 1"
    ).fetchone()
    if last is None:
        return members[0]
    return other_member({"id": last["recommended_by"]}, members)


@app.context_processor
def inject_globals():
    return {"me": current_member()}


# --------------------------------------------------------------------------- #
# Identity gate
# --------------------------------------------------------------------------- #

@app.route("/identity", methods=["GET", "POST"])
def identity():
    if request.method == "POST":
        try:
            mid = int(request.form.get("member_id", 0))
        except (TypeError, ValueError):
            mid = 0
        member = get_db().execute(
            "SELECT * FROM members WHERE id = ?", (mid,)
        ).fetchone()
        if member is None:
            flash("Choose one of the two readers, dear stranger.")
            return redirect(url_for("identity"))
        # Name chosen; the passcode itself is verified on /login.
        session["pending_member_id"] = member["id"]
        return redirect(url_for("login"))
    return render_template("identity.html", members=members_list())


@app.route("/login", methods=["GET", "POST"])
def login():
    member = None
    pid = session.get("pending_member_id")
    if pid:
        member = get_db().execute(
            "SELECT * FROM members WHERE id = ?", (pid,)
        ).fetchone()
    if member is None:
        return redirect(url_for("identity"))
    if request.method == "POST":
        given = (request.form.get("passcode") or "").strip()
        if passcode_for(member) is None:
            flash("The room has no passcode set for this reader yet.")
        elif passcode_ok(member, given):
            session.pop("pending_member_id", None)
            session["member_id"] = member["id"]
            session["authed"] = True
            session.permanent = True
            return redirect(url_for("index"))
        else:
            flash("That passcode does not open this reader's door.")
    return render_template("login.html", member=member)


# --------------------------------------------------------------------------- #
# Dashboard
# --------------------------------------------------------------------------- #

@app.route("/")
def index():
    me = current_member()
    if me is None:
        return redirect(url_for("identity"))
    db = get_db()
    members = members_list()
    partner = other_member(me, members)

    reading = db.execute(
        "SELECT * FROM books WHERE status = 'reading' ORDER BY created_at DESC, id DESC"
    ).fetchall()
    completed = db.execute(
        "SELECT * FROM books WHERE status = 'completed' ORDER BY created_at DESC, id DESC"
    ).fetchall()

    current = reading[0] if reading else None
    current_info = None
    if current:
        emap = essays_map(current["id"])
        recommender = db.execute(
            "SELECT name FROM members WHERE id = ?", (current["recommended_by"],)
        ).fetchone()["name"]
        current_info = {
            "book": current,
            "recommender": recommender,
            "essays": {
                m["name"]: bool(
                    emap.get(m["id"]) and emap[m["id"]]["submitted_at"]
                )
                for m in members
            },
        }

    return render_template(
        "dashboard.html",
        partner=partner,
        current=current_info,
        other_reading=reading[1:],
        completed=completed,
        next_recommender=next_recommender(),
    )


# --------------------------------------------------------------------------- #
# Add a book
# --------------------------------------------------------------------------- #

@app.route("/book/add", methods=["GET", "POST"])
def add_book():
    me = current_member()
    if me is None:
        return redirect(url_for("identity"))
    db = get_db()
    members = members_list()
    default_recommender = next_recommender()

    # Web lookup (Open Library) shown above the manual form.
    lookup_q = (request.args.get("q") or "").strip()
    lookup_results = []
    lookup_failed = False
    if lookup_q and request.method == "GET":
        found = search_books_online(lookup_q)
        if found is None:
            lookup_failed = True
        else:
            lookup_results = found

    # Prefill from a chosen lookup result ("Use this" links pass these back).
    prefill = {
        "title": (request.args.get("title") or "").strip()[:200],
        "author": (request.args.get("author") or "").strip()[:200],
        "cover_url": clean_cover_url(request.args.get("cover_url")),
    }

    if request.method == "POST":
        title = (request.form.get("title") or "").strip()
        author = (request.form.get("author") or "").strip()
        cover_url = clean_cover_url(request.form.get("cover_url"))
        try:
            recommended_by = int(request.form.get("recommended_by", 0))
        except (TypeError, ValueError):
            recommended_by = 0

        valid_ids = {m["id"] for m in members}
        if not title:
            flash("Every book needs a title.")
        elif recommended_by not in valid_ids:
            flash("Choose who recommended this book.")
        else:
            # chapters_total is vestigial (whole books now); kept at 1 to
            # satisfy the legacy NOT NULL / CHECK constraint.
            book_id = insert_returning_id(
                db,
                """INSERT INTO books (title, author, chapters_total, recommended_by, status, created_at, cover_url)
                   VALUES (?, ?, 1, ?, 'reading', ?, ?)""",
                (title, author, recommended_by, now_iso(), cover_url),
            )
            db.commit()
            flash(f"\u2756 {title} rests on the shelf. Read it well, you two.")
            return redirect(url_for("book_detail", book_id=book_id))
        # On validation failure, keep what was typed.
        prefill = {"title": title, "author": author, "cover_url": cover_url}

    return render_template(
        "add_book.html",
        members=members,
        default_recommender=default_recommender,
        lookup_q=lookup_q,
        lookup_results=lookup_results,
        lookup_failed=lookup_failed,
        prefill=prefill,
    )


# --------------------------------------------------------------------------- #
# Book detail
# --------------------------------------------------------------------------- #

@app.route("/book/<int:book_id>")
def book_detail(book_id):
    me = current_member()
    if me is None:
        return redirect(url_for("identity"))
    db = get_db()
    book = get_book_or_404(book_id)
    members = members_list()
    partner = other_member(me, members)

    # One ending essay per reader. States: draft (submitted_at NULL,
    # autosaved to the backend), sealed (submitted, hidden), revealed
    # (both sealed).
    emap = essays_map(book_id)
    my_essay = emap.get(me["id"])
    partner_essay = emap.get(partner["id"])
    my_submitted = bool(my_essay and my_essay["submitted_at"])
    partner_submitted = bool(partner_essay and partner_essay["submitted_at"])
    both_submitted = my_submitted and partner_submitted

    recommender = db.execute(
        "SELECT name FROM members WHERE id = ?", (book["recommended_by"],)
    ).fetchone()["name"]

    edit_essay = request.args.get("edit") == "1"

    return render_template(
        "book.html",
        book=book,
        recommender=recommender,
        partner=partner,
        my_essay=my_essay,
        my_submitted=my_submitted,
        partner_submitted=partner_submitted,
        both_submitted=both_submitted,
        partner_essay=partner_essay if both_submitted else None,
        edit_essay=edit_essay,
    )


# --------------------------------------------------------------------------- #
# Essays (spoiler-safe: revealed only when both are submitted)
#
# States per reader: draft (submitted_at NULL, checkpointed to the backend
# on every write) -> sealed (submitted_at set, hidden) -> revealed (both
# sealed; the book is complete).
# --------------------------------------------------------------------------- #

@app.route("/book/<int:book_id>/essay", methods=["POST"])
def essay(book_id):
    me = current_member()
    if me is None:
        return redirect(url_for("identity"))
    db = get_db()
    book = get_book_or_404(book_id)
    action = request.form.get("action", "save")
    text = (request.form.get("text") or "").strip()
    stamp = now_iso()

    if action == "submit":
        if not text:
            flash("Write something first \u2014 even a rough paragraph will do.")
            return redirect(url_for("book_detail", book_id=book_id))
        db.execute(
            """INSERT INTO essays (member_id, book_id, text, submitted_at, updated_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT (member_id, book_id)
               DO UPDATE SET text = excluded.text, submitted_at = excluded.submitted_at,
                             updated_at = excluded.updated_at""",
            (me["id"], book_id, text, stamp, stamp),
        )
        db.commit()
        both = db.execute(
            "SELECT COUNT(*) AS c FROM essays WHERE book_id = ? AND submitted_at IS NOT NULL",
            (book_id,),
        ).fetchone()["c"]
        if both >= 2:
            db.execute("UPDATE books SET status = 'completed' WHERE id = ?", (book_id,))
            db.commit()
            flash(
                "Both essays are sealed and revealed \u2014 the book now rests "
                "complete on your shelf. \u2766"
            )
        else:
            flash(
                "Your essay is sealed. It will be revealed when your partner's "
                "is complete as well."
            )
    else:
        # Checkpoint: persist the draft on every write so nothing is lost.
        db.execute(
            """INSERT INTO essays (member_id, book_id, text, submitted_at, updated_at)
               VALUES (?, ?, ?, NULL, ?)
               ON CONFLICT (member_id, book_id)
               DO UPDATE SET text = excluded.text, updated_at = excluded.updated_at
               WHERE essays.submitted_at IS NULL""",
            (me["id"], book_id, text, stamp),
        )
        db.commit()

    return redirect(url_for("book_detail", book_id=book_id))


# --------------------------------------------------------------------------- #
# Shelf of completed books
# --------------------------------------------------------------------------- #

@app.route("/shelf")
def shelf():
    me = current_member()
    if me is None:
        return redirect(url_for("identity"))
    db = get_db()
    members = members_list()
    books = db.execute(
        "SELECT * FROM books WHERE status = 'completed' ORDER BY created_at DESC, id DESC"
    ).fetchall()
    emap_all = {}
    for b in books:
        emap_all[b["id"]] = essays_map(b["id"])
    return render_template(
        "shelf.html", books=books, members=members, essays=emap_all
    )


@app.route("/library")
def library_redirect():
    # The library was renamed to the shelf; keep the old door working.
    return redirect(url_for("shelf"))


with app.app_context():
    init_db()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
