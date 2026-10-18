# ShuttleDuel 🏸⚔️

Ranked doubles matchmaking for badminton clubs. Players check in at the courts, queue up, and the system pairs them with opponents of similar skill, assigns courts, and pings each player on Discord when it's their turn.

**English** · [繁體中文](README.zh-TW.md)

> **Status:** the core logic (database, ratings, matchmaking, match flow, notification queue, bilingual messages) is done and tested. The Discord Bot and the web dashboard are in progress.

## Highlights

- **Doubles, first come first served.** Players are matched in check-in order. A team is only skipped when the skill gap is too large, and the allowed gap widens the longer someone waits, so nobody is stuck forever.
- **Elo ratings.** Win and you gain, lose and you drop; upsets move the rating more. Newcomers' first 10 games move faster so ratings settle quickly.
- **Seasons.** Ratings are per season. Each semester the president starts a new season (for example `1151`), adds the new members, and old seasons stay archived.
- **Discord notifications.** Duel invitations, ready buttons and score reports all go through private messages, so the channel stays quiet.
- **Bilingual.** Every player-facing message is in English and Traditional Chinese.
- **Simple to host.** Python + SQLite in one process. No separate database server.

## Project layout

| File | What it does |
|---|---|
| `rating.py` | Elo rating calculation |
| `matching.py` | Matchmaking algorithm (pure functions, no database or Discord) |
| `service.py` | Match flow: queueing, courts, ready timeouts, score reports, admin actions, notification queue. The Bot and the web dashboard only call this layer |
| `messages.py` | All player-facing text (English + 繁體中文) |
| `db.py` | Connection, migrations, transactions, seasons |
| `migrations/` | Database schema, applied in numeric order |
| `test_*.py` | Tests |

## Run the tests

Python 3.10+ is required. No packages need to be installed:

```
python test_db.py
python test_core.py
python test_service.py
python test_messages.py
```

## Match flow

```
queue → matched → [next-up notice] → court frees up → ready check (3 min) → playing → report → opponent confirms → ratings updated
```

- **Next-up notice:** up to 2 upcoming matches are announced while courts are still busy, so the next team can walk over and a court is never left idle.
- **Ready timeout:** if anyone misses the ready check, that match is cancelled. Players who pressed Ready keep their place in the queue and are rematched; players who didn't go to the back of the queue.
- **Reporting:** any player reports the score (21 points, win by 2, capped at 30). Someone on the other team must confirm before ratings change. If they dispute it, the match goes back to in-progress and the admins are notified; an admin can rule on it directly.
- **Report before requeueing:** players who haven't reported their last match can't queue again.
- **President / admins:** cancel a match (no score), rule on a score, change the number of courts, adjust ratings (logged), remove someone from the queue, end the session.

## How the Bot and the web dashboard connect

They are two separate processes sharing one SQLite database. They never call each other.

1. Every action goes through `service.py`. Notifications are not sent directly; they are written to the `notifications` table.
2. The Bot polls `service.pending_notifications()` every few seconds, sends the private messages (text from `messages.render()`), then calls `mark_sent()`. A failed send calls `mark_failed()`; after too many failures it gives up.
3. The Bot also calls `service.tick()` every few seconds to handle ready timeouts and fill free courts.
4. If the Bot restarts, unsent notifications stay in the database and nothing is lost.

## Starting a new season

1. The president starts a new season with a code (for example `1151` = academic year 115, semester 1).
2. The old season is archived automatically and any open session is closed.
3. The president adds this semester's members, optionally with a starting rating (e.g. varsity 1300, beginners 800, everyone else 1000 by default).
4. Members who aren't added don't appear in this season's queue or leaderboard, but their old records are kept.

## Tuning

In `matching.py`:

| Setting | Default | Meaning |
|---|---|---|
| `BASE_WINDOW` | 150 | Allowed rating gap at the start |
| `WINDOW_PER_MIN` | 50 | How much the gap widens per minute of waiting |
| `UNLIMITED_AFTER_MIN` | 10 | After this many minutes the gap is no longer limited |

In `service.py`: `READY_TIMEOUT` (seconds to press Ready), `UPCOMING_LIMIT` (how many next-up matches to announce), `WIN_POINTS` and `MAX_POINTS` (score rules). In `rating.py`: the K factors and how many games count as "new".

## Changing the database

Never edit an old migration. Add the next numbered file (for example `003_something.sql`); it is applied automatically the next time the app starts.

## Privacy & security

- The database (`*.db`) contains members' nicknames and Discord IDs. Never commit it (it is listed in `.gitignore`).
- Keep the Bot token in `.env` and never commit it. If it ever leaks, reset it right away in the Discord Developer Portal.
- Each club creates its own Discord Bot and runs its own copy. Data is never shared between clubs.

## License

No license has been chosen yet. Add a `LICENSE` file before making the repository public.
