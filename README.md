# Seth's WoW Forever Planner

Planner, prep board and live news for World of Warcraft: Forever (launch Nov 4, 2026).

Flask + SQLite, single `app.py`. Port **5858**.

```bash
./run.sh
```

## Pages

- **Dashboard** — countdowns to launch / beta end / raids, latest news, roadmap, what you're rolling
- **News** — RSS pull from six sources, keyword-filtered to Forever, with guild-recruitment
  noise dropped and cross-feed duplicate stories collapsed by title. Polls every 5 min in a
  background thread; the badge and corner toast update every 20s without a reload. Feeds can
  be added, refreshed individually, disabled or deleted in the UI.
- **Planner** — race/class matrix and all four racials per race, Alliance / Horde / both. New combos marked ★. Tucked at the bottom of the nav next to the theme toggle; it's reference, not daily use.
- **My Toons** — character cards, name-reservation state, launch checklist. Each toon has its
  own page at `/toons/<id>` with its notes in blocks, that race's four racials pulled from the
  planner data, and an inline edit form.
- **404** — themed, because everything else was done.

## Notes

Blizzard publishes no working RSS feed. Wowhead's Forever feed carries the blue posts instead.

`data.db` is seeded on first run with the announced races, classes, racials and roadmap. Delete it to reseed.
