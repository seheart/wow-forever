"""Seth's WoW Forever Planner — race/class planner, prep board, live news. Flask + SQLite."""
import random
import re
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import feedparser
from flask import (Flask, abort, g, jsonify, redirect, render_template, request,
                   url_for)

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
        -- Forever is realmless: you pick a ruleset, each one is its own megaserver
        CREATE TABLE IF NOT EXISTS rulesets (
            id INTEGER PRIMARY KEY,
            name TEXT UNIQUE NOT NULL,
            available TEXT DEFAULT 'launch',   -- launch | later
            summary TEXT DEFAULT '',
            good TEXT DEFAULT '',
            bad TEXT DEFAULT '',
            verdict TEXT DEFAULT '',
            chosen INTEGER DEFAULT 0,
            sort INTEGER DEFAULT 0
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
            professions TEXT DEFAULT '',
            priority INTEGER DEFAULT 1,
            reserved INTEGER DEFAULT 0,
            notes TEXT DEFAULT ''
        );
        -- ordered levelling plan per character, tickable as you go
        CREATE TABLE IF NOT EXISTS plan (
            id INTEGER PRIMARY KEY,
            char_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
            ordinal INTEGER DEFAULT 0,
            label TEXT NOT NULL,
            detail TEXT DEFAULT '',
            done INTEGER DEFAULT 0
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
        ("2026-10-27", "Early name reservation opens",
         "Oct 27 - Nov 3 PST. Up to 3 characters, first come first served. "
         "Requires a paid pack ($29.99+). First and second names supported.", 1),
        ("2026-11-04", "Launch", "3:00 PM PST global", 1),
        ("2026-12-09", "Raids open", "Barrow Deeps (10) and Hyjal Summit (20)", 1),
        ("2027-03-01", "Fourth zone", "Spring 2027, nature unconfirmed", 0),
        ("2027-06-01", "More raids + revamped classic raid", "Spring/summer 2027", 0),
    ]
    con.executemany(
        "INSERT INTO roadmap (happens, label, detail, firm) VALUES (?,?,?,?)", roadmap
    )

    con.executemany(
        "INSERT INTO rulesets (name, available, summary, good, bad, verdict, chosen, sort) "
        "VALUES (?,?,?,?,?,?,?,?)",
        [
            ("PvP", "launch",
             "Contested territory is always dangerous. No flagging, no consent, no opting out.",
             "The fights happen to you instead of you hunting for them. World PvP actually exists, "
             "which matters more in Forever than anywhere else because there is no flying to escape "
             "into. Hardcore characters can transfer here later.",
             "Locked to ONE FACTION - you cannot roll Horde alts at all. Faction-balance limits "
             "similar to Season of Discovery may block new character creation if Alliance floods "
             "the ruleset. Thresholds unpublished.",
             "The pick. PvP is the whole reason you are playing, and Normal would make you go "
             "looking for what this hands you.", 1, 1),
            ("Normal", "launch",
             "PvP by mutual consent. You flag when you want a fight.",
             "Both factions available, so alts are unrestricted. Quieter levelling, no ganking "
             "while you are three mobs deep.",
             "You have to go find PvP. World PvP is largely dead on consent realms because the "
             "people who want it are already on PvP.",
             "Wrong ruleset for how you play. Only pick this if you want the levelling to be "
             "calm.", 0, 2),
            ("Roleplay", "launch",
             "Normal rules plus enforced naming conventions and behaviour standards.",
             "Best community and the most immersive open world by a distance.",
             "Strict, GM-enforced naming. Rumpleforeskin and Hotlunch would both be at real risk "
             "here, and a forced rename on a reserved name burns the slot.",
             "Avoid. The naming enforcement alone rules it out for this roster.", 0, 3),
            ("Hardcore", "later",
             "Permanent death, in a closed-off system of its own. 'Winter and beyond'.",
             "The highest-stakes version of the game. For the first time a dead character can "
             "transfer out to PvP rather than only to Normal.",
             "Not available at launch, no date beyond 'winter'. One mistake ends the character.",
             "Not a launch decision. Worth revisiting once it ships, since dead characters can "
             "now land on PvP where you already live.", 0, 4),
        ])

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
    plans = {
        "Chosan": [
            ("Level Beast Mastery, whatever the endgame spec is",
             "The pet tanking is why hunter levels faster than anything else."),
            ("Deadly Aspects", "Damage while Aspect of the Hawk is up, which is always."),
            ("Focused Fire", "Straight damage."),
            ("Summon Hawk", "New in Forever, part of the 1-30 kit."),
            ("Pathfinding", "Run speed is levelling speed."),
            ("Improved Revive Pet", "Quality of life once the above are full."),
            ("Around 40: decide BM or Survival",
             "Survival is the melee build and holds Survival Tactics, which is what makes "
             "the trap/Feign Death escape land."),
        ],
        "Arborna": [
            ("Ferocity (5 points)", "Value in every fight, works in Cat and Bear."),
            ("Heart of the Wild (3 points)", ""),
            ("Feral Swiftness (2 points)", ""),
            ("Mangle when it opens", "The new core Feral attack."),
            ("King of the Jungle", "Energy generation now that Furor is reworked."),
            ("Stop vendoring good weapons",
             "Cat and Bear attacks now scale with equipped weapon damage."),
        ],
    }
    for who, steps in plans.items():
        cid = con.execute("SELECT id FROM characters WHERE name = ?", (who,)).fetchone()
        if cid:
            con.executemany(
                "INSERT INTO plan (char_id, ordinal, label, detail) VALUES (?,?,?,?)",
                [(cid[0], i, l, d) for i, (l, d) in enumerate(steps, 1)])

    con.executemany(
        "INSERT INTO checklist (task, due, sort) VALUES (?,?,?)",
        [
            ("Reserve names: Chosan and Arborna", "2026-10-27", 1),
            ("Decide: buy a Skyborne pack, or stay on sub only", "2026-11-04", 2),
            ("Watch Sept 17 Q&A VOD for a dual spec answer", "", 3),
            ("Check whether Sword Spec crit applies to ranged shots", "", 4),
            ("Pick a realm", "2026-11-04", 5),
            ("Look at Skyborne Druid forms on a beta stream", "2026-11-04", 6),
        ],
    )
    con.commit()


# ---------- news polling ----------
# forum feeds carry a lot of guild recruitment; none of it is news
NOISE = re.compile(
    r"recruit|\bLF[MG]\b|looking for|\bWTS\b|\bWTB\b|boost(ing)?\b"
    r"|\[(NA|EU|OCE|Alliance|Horde|PvP|PvE|RP)\]",
    re.I,
)



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
                if NOISE.search(title):
                    continue
                # two feeds often carry the same story under different urls
                if con.execute("SELECT 1 FROM news WHERE title = ?", (title,)).fetchone():
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
@app.template_filter("when")
def when_filter(raw):
    """Feed dates arrive in a dozen formats. Render whatever parses as '3h ago'."""
    if not raw:
        return ""
    parsed = None
    for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z",
                "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S"):
        try:
            parsed = datetime.strptime(raw.strip(), fmt)
            break
        except ValueError:
            continue
    if parsed is None:
        return raw[:16]
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    mins = (datetime.now(timezone.utc) - parsed).total_seconds() / 60
    if mins < 60:
        return f"{int(max(mins, 0))}m ago"
    if mins < 60 * 48:
        return f"{int(mins // 60)}h ago"
    if mins < 60 * 24 * 14:
        return f"{int(mins // 1440)}d ago"
    return parsed.strftime("%b %-d, %Y")



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
    picked = db().execute("SELECT name FROM rulesets WHERE chosen = 1").fetchone()
    return {"unseen": unseen, "picked": picked["name"] if picked else None}


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
    # mark after reading so this render can still flag what arrived since last visit
    fresh = {r["id"] for r in items if not r["seen"]}
    db().execute("UPDATE news SET seen = 1 WHERE seen = 0")
    db().commit()
    return render_template("news.html", active="news", items=items, feeds=feeds, fresh=fresh)


@app.post("/news/refresh")
def news_refresh():
    fetch_feeds()
    return redirect(url_for("news_page"))


@app.post("/feeds/<int:feed_id>/refresh")
def feed_refresh(feed_id):
    fetch_feeds(only_id=feed_id)
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


@app.get("/realm")
def realm():
    rows = db().execute("SELECT * FROM rulesets ORDER BY sort").fetchall()
    return render_template("realm.html", active="realm", rows=rows)


@app.post("/realm/<int:ruleset_id>/choose")
def realm_choose(ruleset_id):
    db().execute("UPDATE rulesets SET chosen = (id = ?)", (ruleset_id,))
    db().commit()
    return redirect(url_for("realm"))


# ---------- gear: Druid, Alliance, levelling into 20 ----------
# Static reference, not seeded into data.db (so it never needs a reseed).
# Fan-compiled lists (timesaver.gg first, foreverchanges.pro for notes), checked
# 2026-11-11. They disagree in places and drop rates quoted there are Classic numbers.
GEAR_SOURCES = [
    ("timesaver.gg — Druid leveling gear 1-60",
     "https://timesaver.gg/tools/wow-forever/leveling-gear/druid/balance"),
    ("ForeverChanges — Druid BiS, level 20",
     "https://foreverchanges.pro/bis/level-20/druid/balance"),
    ("Wowhead Forever — Westfall",
     "https://www.wowhead.com/forever/zone=40/westfall"),
]

# Each section row: (slot, item, source, where, note)
GEAR_SPECS = {
    "balance": {
        "name": "Balance",
        "role": "Questing now",
        "blurb": "What you level in. Cheap to grab: most of it is Darkshore and Westfall "
                 "quests plus one Deadmines run.",
        "stats": [("Spell Power ≈ Arcane > Nature > Intellect", "Stat priority"),
                  ("Leather over cloth", "Tie-breaker: armour while questing")],
        "sections": [
            ("Before you leave Darkshore",
             "You're in Auberdine now. These are quest rewards right there.", [
                ("Wrist", "Ridgeback Bracers", "Quest: WANTED: Murkdeep!", "Darkshore",
                 "Also the Resto pick, so it covers both specs."),
                ("Hands", "Windfelt Gloves", "Quest: The Absent Minded Prospector",
                 "Darkshore", "Also the Resto pick."),
                ("Waist", "Aquarius Belt", "Druid quest: Aquatic Form", "Class quest, level 16",
                 "You're 18, so it's open. ForeverChanges lists Idol of Shifting Tides "
                 "from the same quest; take whichever the reward offers."),
            ]),
            ("Westfall & the Deadmines", "", [
                ("Feet", "Boots of the People's Militia", "Quest: The People's Militia",
                 "Westfall", "Plain Westfall quest, no dungeon."),
                ("Main Hand (2H)", "Emberstone Staff", "Captain Greenskin", "The Deadmines",
                 "The level-20 Balance weapon. Until it drops, take Staff of Westfall from "
                 "The Defias Brotherhood, which you'll want for Resto anyway."),
                ("Legs", "Dreamer's Leggings", "Quest: Underground Assault", "The Deadmines", ""),
                ("Finger", "Lavishly Jeweled Ring", "Gilnid", "The Deadmines", ""),
                ("Trinket", "Lookie's Spyglass", "Cookie", "The Deadmines", ""),
            ]),
            ("Rest of the level-20 set", "Optional. Worth it if a group forms.", [
                ("Finger", "Minor Channeling Ring", "Quest: WANTED: Chok'sul", "Loch Modan",
                 "Also the Resto pick."),
                ("Neck", "Tarnished Locket", "Quest: Remember That I Love You",
                 "Ruins of Lordaeron", ""),
                ("Shoulder", "Serpent's Shoulders", "Lady Anacondra", "Wailing Caverns", ""),
                ("Back", "Sporid Cape", "Verdan the Everliving", "Wailing Caverns", ""),
                ("Chest", "Armor of the Fang", "Lord Pythas", "Wailing Caverns",
                 "Also the Resto pick."),
                ("Head", "Totemic Leather Hood", "Crafted", "Leatherworking 100",
                 "Crafter-only."),
            ]),
        ],
        "weapons": [(5, "Apprentice's Spellstaff"), (10, "Thornroot Club"),
                    (15, "Fang of Magmatus"), (20, "Emberstone Staff"),
                    (25, "Rod of the Sleepwalker")],
        "next": [
            ("Main Hand (2H)", "Rod of the Sleepwalker", "Twilight Lord Kelris", "Blackfathom Deeps"),
            ("Shoulder", "Executioner Mantle", "Targorr the Dread", "The Stockade"),
            ("Back", "Thornweaver Drape", "Roogug", "Razorfen Kraul"),
            ("Wrist", "Black Wolf Bracers", "Fenrus the Devourer", "Shadowfang Keep"),
            ("Waist", "Girdle of the Blindwatcher", "Odo the Blindwatcher", "Shadowfang Keep"),
            ("Legs", "Dark Ritual Leggings", "Quest: Blackfathom Villainy", "Blackfathom Deeps"),
            ("Feet", "Disjointed Shoes", "Quest: WANTED: Incinerator Gar'im", "Redridge Mountains"),
            ("Finger", "Dark Horde Band", "Targorr the Dread", "The Stockade"),
        ],
    },
    "resto": {
        "name": "Restoration",
        "role": "Healing groups",
        "blurb": "Your real spec. Collect it as you go and keep it in your bags for dungeons, "
                 "since healers get the group invites.",
        "stats": [("Healing ≈ Spell Power > Intellect > Spirit", "Stat priority"),
                  ("Stamina barely matters", "Spell damage counts for nothing")],
        "sections": [
            ("Before you leave Darkshore", "Same two quests as Balance. One reward, both specs.", [
                ("Wrist", "Ridgeback Bracers", "Quest: WANTED: Murkdeep!", "Darkshore", ""),
                ("Hands", "Windfelt Gloves", "Quest: The Absent Minded Prospector",
                 "Darkshore", ""),
            ]),
            ("Westfall & the Deadmines", "", [
                ("Main Hand (2H)", "Staff of Westfall", "Quest: The Defias Brotherhood",
                 "Westfall → The Deadmines",
                 "The Resto weapon. Pick it over Tunic of Westfall. The chain starts at "
                 "Sentinel Hill."),
                ("Feet", "Miner's Workboots", "Quest: Oh Brother...", "The Deadmines", ""),
                ("Legs", "Stonemason Trousers", "Defias Blackguard", "The Deadmines", ""),
                ("Finger", "Lavishly Jeweled Ring", "Gilnid", "The Deadmines", ""),
                ("Trinket", "Lookie's Spyglass", "Cookie", "The Deadmines", ""),
            ]),
            ("Rest of the level-20 set", "", [
                ("Finger", "Minor Channeling Ring", "Quest: WANTED: Chok'sul", "Loch Modan", ""),
                ("Chest", "Armor of the Fang", "Lord Pythas", "Wailing Caverns", ""),
                ("Shoulder", "Serpent's Shoulders", "Lady Anacondra", "Wailing Caverns", ""),
                ("Neck", "Ephemeral Choker", "Faldrim Anvilmar", "The Hall of Thanes",
                 "Hall of Thanes is new in Forever."),
                ("Back", "Spiritwraith Drape", "Faldrim Anvilmar", "The Hall of Thanes",
                 "Same boss as the choker."),
                ("Waist", "Dusty Belt", "Quest: The Restless Dead", "The Hall of Thanes", ""),
                ("Head", "Wisdom's Leather Hood", "Crafted", "Leatherworking 100",
                 "Crafter-only."),
                ("Finger", "Philanthropist's Ring", "Quest: Greater Friend of the Library",
                 "Twenty books", "ForeverChanges' top ring, if you're collecting books."),
            ]),
        ],
        "weapons": [(5, "Apprentice's Spellstaff"), (10, "Thornroot Club"),
                    (15, "Fang of Magmatus"), (20, "Staff of Westfall"),
                    (25, "Odo's Ley Staff")],
        "next": [
            ("Main Hand (2H)", "Odo's Ley Staff", "Odo the Blindwatcher", "Shadowfang Keep"),
            ("Shoulder", "Executioner Mantle", "Targorr the Dread", "The Stockade"),
            ("Back", "Glowing Thresher Cape", "Old Serra'kis", "Blackfathom Deeps"),
            ("Hands", "Naga Battle Gloves", "Lady Sarevess", "Blackfathom Deeps"),
            ("Waist", "Girdle of the Blindwatcher", "Odo the Blindwatcher", "Shadowfang Keep"),
            ("Legs", "Dark Ritual Leggings", "Quest: Blackfathom Villainy", "Blackfathom Deeps"),
            ("Feet", "Ambassador's Boots", "Quest: Crime and Punishment", "The Stockade"),
            ("Trinket", "Worgenbane Talisman", "Commander Springvale", "Shadowfang Keep"),
        ],
    },
    "feral": {
        "name": "Feral",
        "role": "Reference",
        "blurb": "Kept for reference, in case you go Cat or Bear later.",
        "stats": [("Agility > Strength > Stamina", "Stat priority in Cat"),
                  ("Weapon damage first", "Forms scale with it · 14 AP per weapon DPS")],
        "sections": [
            ("Westfall & the Deadmines", "", [
                ("Chest", "Tunic of Westfall", "Quest: The Defias Brotherhood",
                 "Westfall → The Deadmines", "Same quest as Staff of Westfall: one or the other."),
                ("Main Hand (2H)", "Smite's Mighty Hammer", "Mr. Smite", "The Deadmines",
                 "Biggest Feral upgrade; forms scale with weapon damage."),
                ("Wrist", "Bravo's Armbands", "Quest: Underground Assault", "The Deadmines", ""),
                ("Waist", "Blackened Defias Belt", "Captain Greenskin", "The Deadmines", ""),
                ("Finger", "First Mate Band", "Mr. Smite", "The Deadmines", ""),
                ("Finger", "Lavishly Jeweled Ring", "Gilnid", "The Deadmines", ""),
                ("Back", "Cape of the Brotherhood", "Edwin VanCleef", "The Deadmines", ""),
                ("Trinket", "Lookie's Spyglass", "Cookie", "The Deadmines", ""),
            ]),
            ("Rest of the level-20 set", "", [
                ("Relic", "Idol of Shifting Tides", "Druid quest: Aquatic Form",
                 "Class quest, level 16", ""),
                ("Neck", "Erudite's Amulet", "Quest: Friend of the Library", "Ten books", ""),
                ("Head", "Brawler's Leather Hood", "Crafted", "Leatherworking 100", ""),
                ("Shoulder", "Serpent's Shoulders", "Lady Anacondra", "Wailing Caverns", ""),
                ("Hands", "Gloves of the Fang", "Druid of the Fang (trash)", "Wailing Caverns", ""),
                ("Legs", "Leggings of the Fang", "Lord Cobrahn", "Wailing Caverns", ""),
                ("Feet", "Footpads of the Fang", "Lord Serpentis", "Wailing Caverns", ""),
                ("Back", "Glowing Lizardscale Cloak", "Skum", "Wailing Caverns", ""),
            ]),
        ],
        "weapons": [(5, "Northshire Hammer"), (10, "Honed Greathammer"),
                    (15, "Ironforge Greathammer"), (20, "Smite's Mighty Hammer"),
                    (25, "Headbasher")],
        "next": [
            ("Main Hand (2H)", "Headbasher", "Quest: The Fury Runs Deep", "The Stockade"),
            ("Wrist", "Demonhide Bracers", "Quest: Morganth", "Redridge Mountains"),
            ("Legs", "Worgpelt Leggings", "Wolf Master Nandos", "Shadowfang Keep"),
            ("Head", "Geomancer Headdress", "Roogug", "Razorfen Kraul"),
            ("Finger", "Silverlaine's Family Seal", "Baron Silverlaine", "Shadowfang Keep"),
        ],
    },
}


def gear_item_names():
    for s in GEAR_SPECS.values():
        for _, _, rows in s["sections"]:
            yield from (r[1] for r in rows)
        yield from (n for _, n in s["weapons"])
        yield from (r[1] for r in s["next"])


# Wowhead Forever item ids, resolved by exact name from wowhead.com/forever search
# on 2026-11-11. Forever reuses Classic ids for old items; reworked/new ones are 24xxxx+.
WOWHEAD_IDS = {
    "Blackened Defias Belt": 10403, "Bravo's Armbands": 270015,
    "Brawler's Leather Hood": 252504, "Cape of the Brotherhood": 5193,
    "Demonhide Bracers": 270033, "Erudite's Amulet": 277204, "First Mate Band": 284715,
    "Footpads of the Fang": 10411, "Geomancer Headdress": 274155,
    "Gloves of the Fang": 10413, "Glowing Lizardscale Cloak": 6449, "Headbasher": 1264,
    "Honed Greathammer": 257345, "Idol of Shifting Tides": 263411,
    "Ironforge Greathammer": 279895, "Lavishly Jeweled Ring": 1156,
    "Leggings of the Fang": 10410, "Lookie's Spyglass": 273298,
    "Northshire Hammer": 248005, "Serpent's Shoulders": 5404,
    "Silverlaine's Family Seal": 6321, "Smite's Mighty Hammer": 7230,
    "Tunic of Westfall": 2041, "Worgpelt Leggings": 273647,
    # Balance + Resto
    "Ambassador's Boots": 2033, "Apprentice's Spellstaff": 248008, "Aquarius Belt": 16608,
    "Armor of the Fang": 6473, "Black Wolf Bracers": 3230,
    "Boots of the People's Militia": 270006, "Dark Horde Band": 273806,
    "Dark Ritual Leggings": 270031, "Disjointed Shoes": 277226,
    "Dreamer's Leggings": 270016, "Dusty Belt": 279897, "Emberstone Staff": 5201,
    "Ephemeral Choker": 270227, "Executioner Mantle": 273804, "Fang of Magmatus": 271095,
    "Girdle of the Blindwatcher": 6319, "Glowing Thresher Cape": 6901,
    "Miner's Workboots": 270013, "Minor Channeling Ring": 1449, "Naga Battle Gloves": 888,
    "Odo's Ley Staff": 6318, "Philanthropist's Ring": 281635, "Ridgeback Bracers": 15403,
    "Rod of the Sleepwalker": 1155, "Spiritwraith Drape": 271097, "Sporid Cape": 6629,
    "Staff of Westfall": 2042, "Stonemason Trousers": 1934, "Tarnished Locket": 279870,
    "Thornroot Club": 5587, "Thornweaver Drape": 274149, "Totemic Leather Hood": 252448,
    "Windfelt Gloves": 5630, "Wisdom's Leather Hood": 252507,
    "Worgenbane Talisman": 273643,
}


def wowhead_url(name):
    """Item page on Wowhead Forever; falls back to a search if the id isn't known."""
    iid = WOWHEAD_IDS.get(name)
    if iid:
        return f"https://www.wowhead.com/forever/item={iid}"
    return "https://www.wowhead.com/forever/search?q=" + quote(name)


app.jinja_env.globals["wowhead"] = wowhead_url


@app.get("/gear")
def gear():
    spec = request.args.get("spec", "balance")
    if spec not in GEAR_SPECS:
        spec = "balance"
    return render_template("gear.html", active="gear", specs=GEAR_SPECS, spec=spec,
                           s=GEAR_SPECS[spec], sources=GEAR_SOURCES)


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


@app.get("/toons")
def toons():
    chars = db().execute("SELECT * FROM characters ORDER BY priority, id").fetchall()
    tasks = db().execute("SELECT * FROM checklist ORDER BY done, sort, id").fetchall()
    races = db().execute("SELECT name FROM races ORDER BY sort").fetchall()
    return render_template("toons.html", active="toons", chars=chars, tasks=tasks,
                           races=[r["name"] for r in races])


@app.get("/toons/<int:char_id>")
def toon(char_id):
    c = db().execute("SELECT * FROM characters WHERE id = ?", (char_id,)).fetchone()
    if c is None:
        abort(404)
    # pull the race's kit straight off the planner data
    race = db().execute("SELECT * FROM races WHERE name = ?", (c["race"],)).fetchone()
    racials, klasses, playable = [], [], None
    if race:
        racials = db().execute(
            "SELECT * FROM racials WHERE race_id = ? ORDER BY kind DESC, id", (race["id"],)
        ).fetchall()
        klasses = db().execute(
            "SELECT * FROM race_classes WHERE race_id = ? ORDER BY id", (race["id"],)
        ).fetchall()
        playable = next((k for k in klasses if k["klass"].lower() == (c["klass"] or "").lower()), None)
    plan = db().execute(
        "SELECT * FROM plan WHERE char_id = ? ORDER BY ordinal, id", (char_id,)
    ).fetchall()
    # notes are kept as one field, split into blocks on ||
    blocks = [b.strip() for b in (c["notes"] or "").split("||") if b.strip()]
    return render_template("toon.html", active="toons", c=c, race=race, racials=racials,
                           klasses=klasses, playable=playable, blocks=blocks, plan=plan)


@app.post("/toons/<int:char_id>/edit")
def toon_edit(char_id):
    f = request.form
    db().execute(
        "UPDATE characters SET name = ?, race = ?, klass = ?, spec = ?, purpose = ?, "
        "professions = ?, priority = ?, notes = ? WHERE id = ?",
        (f.get("name", "").strip(), f.get("race", "").strip(), f.get("klass", "").strip(),
         f.get("spec", "").strip(), f.get("purpose", "").strip(),
         f.get("professions", "").strip(), int(f.get("priority") or 5),
         f.get("notes", "").strip(), char_id),
    )
    db().commit()
    return redirect(url_for("toon", char_id=char_id))


@app.post("/toons/<int:char_id>/plan/add")
def plan_add(char_id):
    nxt = db().execute(
        "SELECT COALESCE(MAX(ordinal), 0) + 1 n FROM plan WHERE char_id = ?", (char_id,)
    ).fetchone()["n"]
    db().execute(
        "INSERT INTO plan (char_id, ordinal, label, detail) VALUES (?,?,?,?)",
        (char_id, nxt, request.form["label"].strip(), request.form.get("detail", "").strip()),
    )
    db().commit()
    return redirect(url_for("toon", char_id=char_id))


@app.post("/plan/<int:step_id>/toggle")
def plan_toggle(step_id):
    db().execute("UPDATE plan SET done = NOT done WHERE id = ?", (step_id,))
    db().commit()
    return redirect(request.referrer or url_for("toons"))


@app.post("/plan/<int:step_id>/delete")
def plan_delete(step_id):
    db().execute("DELETE FROM plan WHERE id = ?", (step_id,))
    db().commit()
    return redirect(request.referrer or url_for("toons"))


@app.post("/toons/add")
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
    return redirect(url_for("toons"))


@app.post("/toons/<int:char_id>/reserved")
def char_reserved(char_id):
    db().execute("UPDATE characters SET reserved = NOT reserved WHERE id = ?", (char_id,))
    db().commit()
    return redirect(url_for("toons"))


@app.post("/toons/<int:char_id>/delete")
def char_delete(char_id):
    db().execute("DELETE FROM characters WHERE id = ?", (char_id,))
    db().commit()
    return redirect(url_for("toons"))


@app.post("/tasks/add")
def task_add():
    db().execute(
        "INSERT INTO checklist (task, due, sort) VALUES (?,?,?)",
        (request.form["task"].strip(), request.form.get("due", "").strip(), 99),
    )
    db().commit()
    return redirect(url_for("toons"))


@app.post("/tasks/<int:task_id>/toggle")
def task_toggle(task_id):
    db().execute("UPDATE checklist SET done = NOT done WHERE id = ?", (task_id,))
    db().commit()
    return redirect(request.referrer or url_for("toons"))


@app.post("/tasks/<int:task_id>/delete")
def task_delete(task_id):
    db().execute("DELETE FROM checklist WHERE id = ?", (task_id,))
    db().commit()
    return redirect(url_for("toons"))


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


@app.errorhandler(500)
def server_error(_):
    return render_template("404.html", active="", path=request.path,
                           line="The server has encountered a critical error. "
                                "Wipe and try again."), 500


init_db()

if __name__ == "__main__":
    import os
    if os.environ.get("WERKZEUG_RUN_MAIN") == "true" or not app.debug:
        start_poller()
    app.run(debug=True, port=PORT)
