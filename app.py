#!/usr/bin/env python3
"""
The Reading Room -- a private two-person book club.

For Robel (New York) and Bitania (Addis Ababa).

The heart of the app is chapter gating: a member may read chapter k
only once BOTH members have completed chapter k-1. So each reader's
"current chapter" is min(both members' completed chapters) + 1.
"""

import hmac
import os
from datetime import datetime, timedelta, timezone

from flask import (
    Flask, abort, flash, jsonify, redirect, render_template,
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


def progress_map(book_id):
    rows = get_db().execute(
        "SELECT member_id, chapters_completed FROM progress WHERE book_id = ?",
        (book_id,),
    ).fetchall()
    return {r["member_id"]: r["chapters_completed"] for r in rows}


def gate_chapter(pmap):
    """The chapter number either reader may next attempt.

    Chapter k opens only when both members completed k-1, so the
    next open chapter is min(completed) + 1.
    """
    return min(pmap.values()) + 1


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
        pmap = progress_map(current["id"])
        total = current["chapters_total"]
        nxt = gate_chapter(pmap)
        emap = essays_map(current["id"])
        recommender = db.execute(
            "SELECT name FROM members WHERE id = ?", (current["recommended_by"],)
        ).fetchone()["name"]
        current_info = {
            "book": current,
            "recommender": recommender,
            "total": total,
            "next": nxt if nxt <= total else None,
            "progress": [
                {
                    "name": m["name"],
                    "done": pmap.get(m["id"], 0),
                    "pct": round(100 * pmap.get(m["id"], 0) / total),
                }
                for m in members
            ],
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

    if request.method == "POST":
        title = (request.form.get("title") or "").strip()
        author = (request.form.get("author") or "").strip()
        try:
            chapters_total = int(request.form.get("chapters_total", 0))
        except (TypeError, ValueError):
            chapters_total = 0
        try:
            recommended_by = int(request.form.get("recommended_by", 0))
        except (TypeError, ValueError):
            recommended_by = 0

        valid_ids = {m["id"] for m in members}
        if not title:
            flash("Every book needs a title.")
        elif chapters_total < 1:
            flash("Tell us how many chapters the book holds (at least one).")
        elif recommended_by not in valid_ids:
            flash("Choose who recommended this book.")
        else:
            book_id = insert_returning_id(
                db,
                """INSERT INTO books (title, author, chapters_total, recommended_by, status, created_at)
                   VALUES (?, ?, ?, ?, 'reading', ?)""",
                (title, author, chapters_total, recommended_by, now_iso()),
            )
            for m in members:
                db.execute(
                    "INSERT INTO progress (member_id, book_id, chapters_completed) VALUES (?, ?, 0)",
                    (m["id"], book_id),
                )
            db.commit()
            flash(f"\u2756 {title} rests on the shelf. Read in lockstep, you two.")
            return redirect(url_for("book_detail", book_id=book_id))

    return render_template(
        "add_book.html", members=members, default_recommender=default_recommender
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
    pmap = progress_map(book["id"])
    total = book["chapters_total"]
    nxt = gate_chapter(pmap)
    my_done = pmap.get(me["id"], 0)

    chapters = []
    for k in range(1, total + 1):
        done = {m["id"]: pmap.get(m["id"], 0) >= k for m in members}
        if k <= my_done:
            state = "done"
        elif k == nxt and my_done == nxt - 1:
            state = "available"  # my turn to read
        else:
            state = "locked"
        blockers = [m["name"] for m in members if pmap.get(m["id"], 0) < k - 1]
        chapters.append(
            {
                "n": k,
                "done": done,
                "state": state,
                "blockers": blockers,
            }
        )

    notes = {
        r["chapter"]: r["text"]
        for r in db.execute(
            "SELECT chapter, text FROM chapter_notes WHERE book_id = ? AND member_id = ?",
            (book_id, me["id"]),
        ).fetchall()
    }
    book_note_row = db.execute(
        "SELECT text FROM book_notes WHERE book_id = ? AND member_id = ?",
        (book_id, me["id"]),
    ).fetchone()

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
        members=members,
        partner=partner,
        chapters=chapters,
        next_chapter=nxt if nxt <= total else None,
        notes=notes,
        book_note=book_note_row["text"] if book_note_row else "",
        my_essay=my_essay,
        my_submitted=my_submitted,
        partner_submitted=partner_submitted,
        both_submitted=both_submitted,
        partner_essay=partner_essay if both_submitted else None,
        finished_all=my_done >= total,
        edit_essay=edit_essay,
    )


# --------------------------------------------------------------------------- #
# Mark a chapter complete (the gating rule lives here)
# --------------------------------------------------------------------------- #

@app.route("/book/<int:book_id>/complete", methods=["POST"])
def complete_chapter(book_id):
    me = current_member()
    if me is None:
        return redirect(url_for("identity"))
    db = get_db()
    book = get_book_or_404(book_id)
    pmap = progress_map(book_id)
    nxt = gate_chapter(pmap)
    my_done = pmap.get(me["id"], 0)

    # Only the reader who is behind (or tied) may advance, exactly one chapter.
    if my_done != nxt - 1 or nxt > book["chapters_total"]:
        flash("That chapter is not yet open to you \u2014 patience, reader.")
        return redirect(url_for("book_detail", book_id=book_id))

    db.execute(
        "UPDATE progress SET chapters_completed = ? WHERE book_id = ? AND member_id = ?",
        (nxt, book_id, me["id"]),
    )
    db.commit()

    if nxt == book["chapters_total"]:
        flash(
            f"You have turned the final page of Chapter {to_roman(nxt)}. "
            "Now chew into the book \u2014 write your essay below."
        )
    else:
        flash(f"Chapter {to_roman(nxt)} complete. Well read.")
    return redirect(url_for("book_detail", book_id=book_id))


# --------------------------------------------------------------------------- #
# Notes (autosaved, private per member)
# --------------------------------------------------------------------------- #

@app.route("/book/<int:book_id>/chapter-note", methods=["POST"])
def save_chapter_note(book_id):
    me = current_member()
    if me is None:
        return jsonify({"ok": False, "error": "unknown reader"}), 401
    book = get_book_or_404(book_id)
    try:
        chapter = int(request.form.get("chapter", 0))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "bad chapter"}), 400
    if not 1 <= chapter <= book["chapters_total"]:
        return jsonify({"ok": False, "error": "bad chapter"}), 400
    text = request.form.get("text", "")
    db = get_db()
    db.execute(
        """INSERT INTO chapter_notes (member_id, book_id, chapter, text, updated_at)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT (member_id, book_id, chapter)
           DO UPDATE SET text = excluded.text, updated_at = excluded.updated_at""",
        (me["id"], book_id, chapter, text, now_iso()),
    )
    db.commit()
    return jsonify({"ok": True})


@app.route("/book/<int:book_id>/book-note", methods=["POST"])
def save_book_note(book_id):
    me = current_member()
    if me is None:
        return jsonify({"ok": False, "error": "unknown reader"}), 401
    get_book_or_404(book_id)
    text = request.form.get("text", "")
    db = get_db()
    db.execute(
        """INSERT INTO book_notes (member_id, book_id, text, updated_at)
           VALUES (?, ?, ?, ?)
           ON CONFLICT (member_id, book_id)
           DO UPDATE SET text = excluded.text, updated_at = excluded.updated_at""",
        (me["id"], book_id, text, now_iso()),
    )
    db.commit()
    return jsonify({"ok": True})


# --------------------------------------------------------------------------- #
# Essays (spoiler-safe: revealed only when both are submitted)
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

    if action == "submit":
        if not text:
            flash("Write something first \u2014 even a rough paragraph will do.")
            return redirect(url_for("book_detail", book_id=book_id))
        db.execute(
            """INSERT INTO essays (member_id, book_id, text, submitted_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT (member_id, book_id)
               DO UPDATE SET text = excluded.text, submitted_at = excluded.submitted_at""",
            (me["id"], book_id, text, now_iso()),
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
                "complete in your library. \u2766"
            )
        else:
            flash(
                "Your essay is sealed. It will be revealed when your partner's "
                "is complete as well."
            )
    else:
        db.execute(
            """INSERT INTO essays (member_id, book_id, text, submitted_at)
               VALUES (?, ?, ?, NULL)
               ON CONFLICT (member_id, book_id)
               DO UPDATE SET text = excluded.text""",
            (me["id"], book_id, text),
        )
        db.commit()

    return redirect(url_for("book_detail", book_id=book_id))


# --------------------------------------------------------------------------- #
# Library of completed books
# --------------------------------------------------------------------------- #

@app.route("/library")
def library():
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
        "library.html", books=books, members=members, essays=emap_all
    )


with app.app_context():
    init_db()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
