---
id: daily-standup
name: Daily Standup Context
scope: infinite
agent: proxy
status: active
triggers:
  - source: schedule
    event: cron
    filter:
      schedule_name: daily_standup
soft_triggers:
  - keywords: ["standup", "daily", "what did", "blockers", "today"]
---

# Daily Standup

When preparing or participating in a daily standup:

1. Summarize completed work items from the last 24 hours
2. List in-progress items and their current status
3. Flag any blockers or items needing escalation
4. Check calendar for meetings that might affect availability

Keep updates concise — aim for 2-3 bullet points per section.
