---
name: "daily-digest"
description: "Assemble and deliver the morning digest: open tasks, calendar, overnight pipeline results, anything that needs a decision today."
---

# Daily Digest

Runs unattended from the scheduler at 09:00 local time and delivers one message
to the owner's chat. Designed so the agent does the reading and the human only
does the deciding.

## Flow

```
scheduler fires (cron, isolated session)
  → read TASKS.md
  → semantic search over memory/ for anything marked blocked or waiting
  → pull overnight pipeline output from the work host
  → drop anything already reported yesterday and unchanged
  → rank: needs a decision > deadline today > in progress > backlog
  → deliver one message; end with a single question
```

## Rules that make it survivable

- **One message, not five.** A digest that arrives in fragments gets ignored.
- **No "no news" reports.** If nothing moved and nothing is due, stay silent.
- **Carry the why.** "Blocked on X since Tuesday" beats "Blocked".
- **Close the loop with a question.** The last line is always
  "what are we picking up today?" — the digest exists to start work, not to
  announce itself.
- **Quiet hours.** Nothing between 23:00 and 08:00 unless it is genuinely urgent.

## Scheduling

Registered as a cron job against an **isolated** session, so a long digest never
pollutes the main conversation's context:

```json
{
  "name": "daily-digest",
  "schedule": { "kind": "cron", "expr": "0 9 * * *", "tz": "YOUR_TIMEZONE" },
  "sessionTarget": "isolated",
  "payload": { "kind": "agentTurn", "message": "Run the daily-digest skill." },
  "delivery": { "mode": "announce", "channel": "telegram", "to": "YOUR_CHAT_ID" }
}
```

## Failure behaviour

If a source is unreachable, the digest still ships — with that section marked
`unavailable` and the reason. A partial digest on time is worth more than a
complete one that never arrives.
