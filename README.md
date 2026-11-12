# Seth's WoW Forever Planner

Prep board, toons and gear for World of Warcraft: Forever, on the PvP ruleset (launch Nov 4, 2026).

Flask + SQLite, single `app.py`. Port **5858**.

```bash
./run.sh
```

## Pages

- **Dashboard** — countdowns to launch / beta end / raids, roadmap, what you're rolling
- **My Toons** — character cards, name-reservation state, launch checklist. Each toon has its
  own page at `/toons/<id>` with its notes in blocks, that race's four racials, and an inline
  edit form. Toons of a class with a gear list get `/toons/<id>/gear`: level-20 best in slot
  with a tab per spec, every item linked to Wowhead Forever with hover tooltips (druid for now).
- **404** — themed, because everything else was done.

## Notes

`data.db` is seeded on first run with the announced races, classes, racials and roadmap. Delete it to reseed.
