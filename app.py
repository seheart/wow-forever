"""Seth's WoW Forever Planner — race/class planner, prep board, live news. Flask + SQLite."""
import random
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import feedparser
from flask import Flask, g, jsonify, redirect, render_template, request, url_for

APP_DIR = Path(__file__).parent
DB_PATH = APP_DIR / "data.db"
PORT = 5858

# launch: November 4 2026, 3:00 PM PST == 23:00 UTC
LAUNCH = datetime(2026, 11, 4, 23, 0, tzinfo=timezone.utc)
BETA_END = datetime(2026, 10, 21, 23, 0, tzinfo=timezone.utc)
RAIDS = datetime(2026, 12, 9, 23, 0, tzinfo=timezone.utc)

POLL_SECONDS = 300

app = Flask(__name__)


# ---------- db ----------
def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_=None):
    d = g.pop("db", None)
    if d is not None:
        d.close()


def init_db():
    con = sqlite3.connect(DB_PATH)
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS feeds (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            url TEXT UNIQUE NOT NULL,
            keyword TEXT DEFAULT '',      -- only keep entries mentioning this
            enabled INTEGER DEFAULT 1,
            last_checked TEXT DEFAULT '',
            last_status TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS news (
            id INTEGER PRIMARY KEY,
            feed_id INTEGER REFERENCES feeds(id) ON DELETE CASCADE,
            source TEXT DEFAULT '',
            title TEXT NOT NULL,
            url TEXT UNIQUE NOT NULL,
            published TEXT DEFAULT '',
            fetched TEXT DEFAULT '',
            seen INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS roadmap (
            id INTEGER PRIMARY KEY,
            happens TEXT NOT NULL,        -- YYYY-MM-DD
            label TEXT NOT NULL,
            detail TEXT DEFAULT '',
            firm INTEGER DEFAULT 1        -- 0 = window/estimate
        );
        CREATE TABLE IF NOT EXISTS races (
            id INTEGER PRIMARY KEY,
            slug TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            faction TEXT NOT NULL,        -- Alliance|Horde
            note TEXT DEFAULT '',
            sort INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS race_classes (
            id INTEGER PRIMARY KEY,
            race_id INTEGER NOT NULL REFERENCES races(id) ON DELETE CASCADE,
            klass TEXT NOT NULL,
            is_new INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS racials (
            id INTEGER PRIMARY KEY,
            race_id INTEGER NOT NULL REFERENCES races(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            kind TEXT DEFAULT 'passive',  -- active|passive
            effect TEXT DEFAULT '',
            tag TEXT DEFAULT ''           -- new|reworked|vanilla
        );
        -- prep board
        CREATE TABLE IF NOT EXISTS characters (
            id INTEGER PRIMARY KEY,
            name TEXT DEFAULT '',
            race TEXT DEFAULT '',
            klass TEXT DEFAULT '',
            spec TEXT DEFAULT '',
            purpose TEXT DEFAULT '',
            priority INTEGER DEFAULT 1,
            reserved INTEGER DEFAULT 0,
            notes TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS checklist (
            id INTEGER PRIMARY KEY,
            task TEXT NOT NULL,
            due TEXT DEFAULT '',
            done INTEGER DEFAULT 0,
            sort INTEGER DEFAULT 0
        );
        """
    )
    con.commit()
    if not con.execute("SELECT 1 FROM races LIMIT 1").fetchone():
        seed(con)
    con.close()


def seed(con):
    # Blizzard publishes no working RSS — Wowhead's Forever feed carries the blue posts.
    feeds = [
        ("Wowhead Forever", "https://www.wowhead.com/forever/news/rss/all", ""),
        ("Wowhead", "https://www.wowhead.com/news/rss/all", "forever"),
        ("Blizzard Watch", "https://blizzardwatch.com/feed/", "forever"),
        ("Warcraft Tavern", "https://www.warcrafttavern.com/feed/", "forever"),
        ("r/classicwow", "https://www.reddit.com/r/classicwow/.rss", "forever"),
        ("MMO-Champion", "https://www.mmo-champion.com/external.php?type=RSS2", "forever"),
    ]
    con.executemany("INSERT OR IGNORE INTO feeds (name, url, keyword) VALUES (?,?,?)", feeds)

    roadmap = [
        ("2026-09-12", "Announced at BlizzCon 2026", "Revealed alongside Midnight", 1),
        ("2026-09-17", "Beta opens + live Q&A", "Level 20 cap at open; Q&A 10:30am PDT", 1),
        ("2026-10-21", "Beta ends", "Cap raised to 30 partway through", 1),
        ("2026-11-04", "Launch", "3:00 PM PST global", 1),
        ("2026-12-09", "Raids open", "Barrow Deeps (10) and Hyjal Summit (20)", 1),
        ("2027-03-01", "Fourth zone", "Spring 2027, nature unconfirmed", 0),
        ("2027-06-01", "More raids + revamped classic raid", "Spring/summer 2027", 0),
    ]
    con.executemany(
        "INSERT INTO roadmap (happens, label, detail, firm) VALUES (?,?,?,?)", roadmap
    )

    races = [
        # slug, name, faction, note, sort, classes[(name,new)], racials[(name,kind,effect,tag)]
        ("human", "Human", "Alliance", "", 1,
         [("Warrior", 0), ("Paladin", 0), ("Hunter", 1), ("Rogue", 0),
          ("Priest", 0), ("Mage", 0), ("Warlock", 0)],
         [("Will to Survive", "active", "Removes all stun effects.", "new"),
          ("Perception", "active", "Detect stealthed enemies for 20 sec.", "vanilla"),
          ("Sword Specialization", "passive", "Swords increase spell and ability crit chance by 2%.", "reworked"),
          ("The Human Spirit", "passive", "Increases Spirit by 5%.", "vanilla")]),
        ("dwarf", "Dwarf", "Alliance", "", 2,
         [("Warrior", 0), ("Paladin", 0), ("Hunter", 0), ("Rogue", 0),
          ("Priest", 0), ("Shaman", 1)],
         [("Stoneform", "active", "Immune to bleed, poison and disease; reduces physical damage taken, 8 sec.", "reworked"),
          ("Find Treasure", "active", "Track nearby treasure chests.", "vanilla"),
          ("Mace Specialization", "passive", "Maces increase spell and ability crit chance by 1%.", "new"),
          ("Big Game Hunter", "passive", "Increases damage dealt to Beasts by 5%.", "new")]),
        ("night-elf", "Night Elf", "Alliance", "No new class combo in Forever.", 3,
         [("Warrior", 0), ("Hunter", 0), ("Rogue", 0), ("Priest", 0), ("Druid", 0)],
         [("Elune's Light", "active", "Increases crit chance by 10% for 15 sec.", "new"),
          ("Shadowmeld", "active", "Gain stealth while stationary. Out of combat only.", "vanilla"),
          ("Quickness", "passive", "1% dodge and 2% run speed.", "reworked"),
          ("Wisp Spirit", "passive", "75% run speed while dead.", "vanilla")]),
        ("gnome", "Gnome", "Alliance", "", 4,
         [("Warrior", 0), ("Rogue", 0), ("Priest", 1), ("Mage", 0), ("Warlock", 0)],
         [("Escape Artist", "active", "Break roots and snares.", "vanilla"),
          ("Eureka!", "active", "Next 3 spells or abilities deal 10% more damage or healing.", "new"),
          ("Expansive Mind", "passive", "Increases maximum resource pool by 5%.", "new"),
          ("Engineering Specialization", "passive", "Engineering devices are more reliable.", "reworked")]),
        ("skyborne-alliance", "Skyborne (Alliance)", "Alliance", "Behind the $29.99 Skyborne Heroic Pack.", 5,
         [("Warrior", 0), ("Hunter", 0), ("Rogue", 0), ("Druid", 0), ("Mage", 0)],
         [("Walk on Air", "active", "Glide on air for 10 sec.", "new"),
          ("Read Ley Line", "active", "Activate ley lines across Azeroth for 100% health and mana regen.", "new"),
          ("Wind Blessed", "passive", "1% increased melee, ranged and casting haste.", "new"),
          ("Elemental Insight", "passive", "5% increased damage to Elementals.", "new")]),
        ("orc", "Orc", "Horde", "", 6,
         [("Warrior", 0), ("Hunter", 0), ("Rogue", 0), ("Shaman", 0),
          ("Warlock", 0), ("Mage", 1)],
         [("Blood Fury", "active", "Attack power cooldown.", "vanilla"),
          ("Shatter Curse", "active", "Immune to Curses and Banes; reduces magic damage taken, 8 sec.", "new"),
          ("Hardiness", "passive", "Resistance to stun effects.", "vanilla"),
          ("Axe Specialization", "passive", "Axes increase crit chance by 1%.", "reworked")]),
        ("undead", "Undead", "Horde", "", 7,
         [("Warrior", 0), ("Rogue", 0), ("Priest", 0), ("Mage", 0),
          ("Warlock", 0), ("Paladin", 1)],
         [("Will of the Forsaken", "active", "On-use removal of charm, fear and sleep. No longer an immunity window.", "reworked"),
          ("Cannibalize", "active", "Restore health by feeding on corpses.", "vanilla"),
          ("Touch of the Grave", "passive", "5% chance for attacks to drain health.", "new"),
          ("Underwater Breathing", "passive", "Greatly increased breath.", "vanilla")]),
        ("tauren", "Tauren", "Horde", "No new class combo in Forever.", 8,
         [("Warrior", 0), ("Hunter", 0), ("Shaman", 0), ("Druid", 0)],
         [("War Stomp", "active", "Stun nearby enemies.", "vanilla"),
          ("Plainsrunning", "active", "Movement speed builds the longer you move.", "new"),
          ("Endurance", "passive", "Increased health and 1% hit chance.", "reworked"),
          ("Cultivation", "passive", "Pick bonus herbs even without Herbalism.", "reworked")]),
        ("troll", "Troll", "Horde", "", 9,
         [("Warrior", 0), ("Hunter", 0), ("Rogue", 0), ("Priest", 0),
          ("Shaman", 0), ("Mage", 0), ("Warlock", 1)],
         [("Berserking", "active", "Haste cooldown scaling with missing health.", "vanilla"),
          ("Rapid Regeneration", "active", "Regenerate 50% of maximum health over time.", "reworked"),
          ("Regeneration", "passive", "10% health regeneration continues in combat.", "vanilla"),
          ("Beast Slaying", "passive", "Increased damage to Beasts.", "vanilla")]),
        ("skyborne-horde", "Skyborne (Horde)", "Horde", "Behind the $29.99 Skyborne Heroic Pack.", 10,
         [("Warrior", 0), ("Hunter", 0), ("Rogue", 0), ("Druid", 0), ("Shaman", 0)],
         [("Walk on Air", "active", "Glide on air for 10 sec.", "new"),
          ("Skysight", "active", "10% movement speed increase.", "new"),
          ("Wind Blessed", "passive", "1% increased melee, ranged and casting haste.", "new"),
          ("Elemental Insight", "passive", "5% increased damage to Elementals.", "new")]),
    ]
    for slug, name, faction, note, sort, classes, racials in races:
        cur = con.execute(
            "INSERT INTO races (slug, name, faction, note, sort) VALUES (?,?,?,?,?)",
            (slug, name, faction, note, sort),
        )
        rid = cur.lastrowid
        con.executemany(
            "INSERT INTO race_classes (race_id, klass, is_new) VALUES (?,?,?)",
            [(rid, k, n) for k, n in classes],
        )
        con.executemany(
            "INSERT INTO racials (race_id, name, kind, effect, tag) VALUES (?,?,?,?,?)",
            [(rid, n, k, e, t) for n, k, e, t in racials],
        )

    con.executemany(
        "INSERT INTO characters (name, race, klass, spec, purpose, priority, notes) "
        "VALUES (?,?,?,?,?,?,?)",
        [
            ("Chosan", "Human", "Hunter", "Marksmanship", "Main - PvP and questing", 1,
             "Will to Survive breaks stuns without burning the PvP trinket; Perception sees Rogue openers. "
             "New combo in Forever."),
            ("Arborna", "Night Elf", "Druid", "Feral", "The class I actually love", 2,
             "Elune's Light is a 15 sec +10% crit cooldown - good on feral openers and on heals. "
             "Skyborne Druid is the paid alternative if the custom forms land well."),
        ],
    )
    con.executemany(
        "INSERT INTO checklist (task, due, sort) VALUES (?,?,?)",
        [
            ("Reserve character names", "2026-11-04", 1),
            ("Decide: buy a Skyborne pack, or stay on sub only", "2026-11-04", 2),
            ("Watch Sept 17 Q&A VOD for a dual spec answer", "", 3),
            ("Check whether Sword Spec crit applies to ranged shots", "", 4),
            ("Pick a realm", "2026-11-04", 5),
            ("Look at Skyborne Druid forms on a beta stream", "2026-11-04", 6),
        ],
    )
    con.commit()


# ---------- news polling ----------
def fetch_feeds(only_id=None):
    """Pull every enabled feed. Returns how many new items landed."""
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    q = "SELECT * FROM feeds WHERE enabled = 1"
    rows = con.execute(q + (" AND id = ?" if only_id else ""),
                       (only_id,) if only_id else ()).fetchall()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    added = 0
    for f in rows:
        status = "ok"
        try:
            parsed = feedparser.parse(f["url"])
            if parsed.bozo and not parsed.entries:
                status = f"error: {parsed.bozo_exception}"[:120]
            kw = (f["keyword"] or "").lower()
            for e in parsed.entries[:60]:
                title = (e.get("title") or "").strip()
                link = (e.get("link") or "").strip()
                if not title or not link:
                    continue
                blob = f"{title} {e.get('summary', '')}".lower()
                if kw and kw not in blob:
                    continue
                pub = e.get("published") or e.get("updated") or ""
                cur = con.execute(
                    "INSERT OR IGNORE INTO news (feed_id, source, title, url, published, fetched) "
                    "VALUES (?,?,?,?,?,?)",
                    (f["id"], f["name"], title, link, pub, now),
                )
                added += cur.rowcount
        except Exception as exc:  # a dead feed should never take the poller down
            status = f"error: {exc}"[:120]
        con.execute("UPDATE feeds SET last_checked = ?, last_status = ? WHERE id = ?",
                    (now, status, f["id"]))
    con.commit()
    con.close()
    return added


def poller():
    while True:
        try:
            fetch_feeds()
        except Exception:
            pass
        time.sleep(POLL_SECONDS)


def start_poller():
    threading.Thread(target=poller, daemon=True).start()


# ---------- helpers ----------
def countdowns():
    now = datetime.now(timezone.utc)
    def delta(target):
        s = (target - now).total_seconds()
        return {"days": int(abs(s) // 86400), "hours": int((abs(s) % 86400) // 3600),
                "past": s < 0}
    return {"launch": delta(LAUNCH), "beta_end": delta(BETA_END), "raids": delta(RAIDS)}


@app.context_processor
def inject_nav():
    unseen = db().execute("SELECT COUNT(*) c FROM news WHERE seen = 0").fetchone()["c"]
    return {"unseen": unseen}


# ---------- routes ----------
@app.route("/")
def dashboard():
    news = db().execute(
        "SELECT * FROM news ORDER BY id DESC LIMIT 12"
    ).fetchall()
    road = db().execute("SELECT * FROM roadmap ORDER BY happens").fetchall()
    chars = db().execute(
        "SELECT * FROM characters ORDER BY priority, id"
    ).fetchall()
    todo = db().execute(
        "SELECT * FROM checklist WHERE done = 0 ORDER BY sort, id"
    ).fetchall()
    return render_template("dashboard.html", active="dashboard", news=news, road=road,
                           chars=chars, todo=todo, cd=countdowns(),
                           today=datetime.now().strftime("%Y-%m-%d"))


@app.route("/news")
def news_page():
    items = db().execute("SELECT * FROM news ORDER BY id DESC LIMIT 200").fetchall()
    feeds = db().execute("SELECT * FROM feeds ORDER BY name").fetchall()
    db().execute("UPDATE news SET seen = 1 WHERE seen = 0")
    db().commit()
    return render_template("news.html", active="news", items=items, feeds=feeds)


@app.post("/news/refresh")
def news_refresh():
    fetch_feeds()
    return redirect(url_for("news_page"))


@app.post("/feeds/add")
def feed_add():
    db().execute(
        "INSERT OR IGNORE INTO feeds (name, url, keyword) VALUES (?,?,?)",
        (request.form["name"].strip(), request.form["url"].strip(),
         request.form.get("keyword", "").strip()),
    )
    db().commit()
    return redirect(url_for("news_page"))


@app.post("/feeds/<int:feed_id>/toggle")
def feed_toggle(feed_id):
    db().execute("UPDATE feeds SET enabled = NOT enabled WHERE id = ?", (feed_id,))
    db().commit()
    return redirect(url_for("news_page"))


@app.post("/feeds/<int:feed_id>/delete")
def feed_delete(feed_id):
    db().execute("DELETE FROM feeds WHERE id = ?", (feed_id,))
    db().commit()
    return redirect(url_for("news_page"))


@app.get("/api/news")
def api_news():
    """Poll target for the live badge — newest id and unseen count."""
    row = db().execute(
        "SELECT COALESCE(MAX(id), 0) top, SUM(seen = 0) unseen FROM news"
    ).fetchone()
    latest = db().execute(
        "SELECT title, url, source FROM news ORDER BY id DESC LIMIT 5"
    ).fetchall()
    return jsonify({
        "top": row["top"],
        "unseen": row["unseen"] or 0,
        "latest": [dict(r) for r in latest],
    })


@app.get("/planner")
def planner():
    faction = request.args.get("faction", "Alliance")
    where = "" if faction == "all" else "WHERE faction = ?"
    args = () if faction == "all" else (faction,)
    races = db().execute(f"SELECT * FROM races {where} ORDER BY sort", args).fetchall()
    out = []
    for r in races:
        out.append({
            "race": r,
            "classes": db().execute(
                "SELECT * FROM race_classes WHERE race_id = ? ORDER BY id", (r["id"],)
            ).fetchall(),
            "racials": db().execute(
                "SELECT * FROM racials WHERE race_id = ? ORDER BY kind DESC, id", (r["id"],)
            ).fetchall(),
        })
    all_classes = [c["klass"] for c in db().execute(
        "SELECT DISTINCT klass FROM race_classes ORDER BY klass").fetchall()]
    return render_template("planner.html", active="planner", rows=out,
                           faction=faction, all_classes=all_classes)


@app.get("/prep")
def prep():
    chars = db().execute("SELECT * FROM characters ORDER BY priority, id").fetchall()
    tasks = db().execute("SELECT * FROM checklist ORDER BY done, sort, id").fetchall()
    races = db().execute("SELECT name FROM races ORDER BY sort").fetchall()
    return render_template("prep.html", active="prep", chars=chars, tasks=tasks,
                           races=[r["name"] for r in races])


@app.post("/prep/characters/add")
def char_add():
    f = request.form
    db().execute(
        "INSERT INTO characters (name, race, klass, spec, purpose, priority, notes) "
        "VALUES (?,?,?,?,?,?,?)",
        (f.get("name", "").strip(), f.get("race", "").strip(), f.get("klass", "").strip(),
         f.get("spec", "").strip(), f.get("purpose", "").strip(),
         int(f.get("priority") or 5), f.get("notes", "").strip()),
    )
    db().commit()
    return redirect(url_for("prep"))


@app.post("/prep/characters/<int:char_id>/reserved")
def char_reserved(char_id):
    db().execute("UPDATE characters SET reserved = NOT reserved WHERE id = ?", (char_id,))
    db().commit()
    return redirect(url_for("prep"))


@app.post("/prep/characters/<int:char_id>/delete")
def char_delete(char_id):
    db().execute("DELETE FROM characters WHERE id = ?", (char_id,))
    db().commit()
    return redirect(url_for("prep"))


@app.post("/prep/tasks/add")
def task_add():
    db().execute(
        "INSERT INTO checklist (task, due, sort) VALUES (?,?,?)",
        (request.form["task"].strip(), request.form.get("due", "").strip(), 99),
    )
    db().commit()
    return redirect(url_for("prep"))


@app.post("/prep/tasks/<int:task_id>/toggle")
def task_toggle(task_id):
    db().execute("UPDATE checklist SET done = NOT done WHERE id = ?", (task_id,))
    db().commit()
    return redirect(request.referrer or url_for("prep"))


@app.post("/prep/tasks/<int:task_id>/delete")
def task_delete(task_id):
    db().execute("DELETE FROM checklist WHERE id = ?", (task_id,))
    db().commit()
    return redirect(url_for("prep"))


# ---------- 404 ----------
NOT_FOUND = [
    "Your corpse is in another zone.",
    "You must be level 60 to view this page.",
    "Out of range.",
    "You can't do that while dead.",
    "That page is behind the $29.99 Skyborne Heroic Pack.",
    "This page requires Beta access. Your pack tier does not include Beta access.",
    "Spell is not ready yet.",
    "There is nothing to loot.",
    "You have no target.",
    "You are in combat.",
    "Not enough rage.",
    "That page was cut in the talent pass. It was one of the 81.",
    "This page is unannounced as of today. Blizzard has not confirmed it, and has not denied it.",
    "A Rogue opened on this page from stealth. Nothing survived.",
    "You have been disconnected from the server. Investigating some issues with people being able to get into realms now.",
    "Queue position: 4,182. Estimated time: 43 minutes.",
    "Ability is not available in this expansion.",
    "You are not in the right faction for this page.",
]


@app.errorhandler(404)
def not_found(_):
    return render_template("404.html", active="", line=random.choice(NOT_FOUND),
                           path=request.path), 404


init_db()

if __name__ == "__main__":
    import os
    if os.environ.get("WERKZEUG_RUN_MAIN") == "true" or not app.debug:
        start_poller()
    app.run(debug=True, port=PORT)
