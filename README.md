# Seth's WoW Forever Planner

Planner, prep board and live news for World of Warcraft: Forever (launch Nov 4, 2026).

Flask + SQLite, single `app.py`. Port **5858**.

```bash
./run.sh
```

## Pages

- **Dashboard** — countdowns to launch / beta end / raids, latest news, roadmap, what you're rolling
- **News** — RSS pull from six sources, keyword-filtered to Forever. Polls every 5 min in a background thread; the page badge and corner toast update every 20s without a reload. Add/disable/delete feeds in the UI.
- **Planner** — race/class matrix and all four racials per race, Alliance / Horde / both. New combos marked ★.
- **Prep** — characters to roll, name-reservation state, launch checklist.

## Notes

Blizzard publishes no working RSS feed. Wowhead's Forever feed carries the blue posts instead.

`data.db` is seeded on first run with the announced races, classes, racials and roadmap. Delete it to reseed.
