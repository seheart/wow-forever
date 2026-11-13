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
  edit form, plus level, XP to next level and a character-sheet stats snapshot. Toons of a class
  with a gear list get `/toons/<id>/gear`: level-20 best in slot with a tab per spec, every item
  linked to Wowhead Forever with hover tooltips (druid for now), and a "Wearing now" table that
  compares equipped items against BiS. Paste the output of
  `/run for i=1,19 do local l=GetInventoryItemLink("player",i) if l then print(i,l) end end`
  to update it.
- **404** — themed, because everything else was done.

## Notes

`data.db` is seeded on first run with the announced races, classes, racials and roadmap. Delete it to reseed.
