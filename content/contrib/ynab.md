# ynab.json

Net worth tracker from YNAB (You Need A Budget). Hobbies — fires once daily.

## Schedule

### `net_worth` — net worth and monthly change

**Cron:** `15 11 * * *` — fires daily at 11:15.
**Hold:** 300 s | **Timeout:** 3600 s | **Priority:** 3 (Hobbies)

Private template. Fires in a solo `:15` slot with a 300 s hold budget — well
within the 1800 s ceiling.

To override schedule fields without editing this file:

```toml
[ynab.schedules.net_worth]
cron = "15 8,20 * * *"  # check twice daily
hold = 300
timeout = 3600
disabled = true           # skip entirely
```

## Configuration

Add the following to your `config.toml`:

```toml
[ynab]
api_key = "your-personal-access-token"
# budget_id = "your-budget-id"  # optional if you have only one budget
```

| Key | Required | Description |
|---|---|---|
| `api_key` | Yes | Personal access token from YNAB Settings → Developer Settings |
| `budget_id` | No | Budget UUID — auto-detected when you have one budget. Required when you have multiple. Find via the YNAB web app URL (`app.ynab.com/<budget_id>/budget`) |

## Display format

```
[G] NET WORTH
$124,832
+2.6%
```

- Header color is data-driven: `[G]` green when month-over-month delta is
  non-negative, `[R]` red when negative (or when net worth itself is negative)
- Net worth with commas for amounts under $10K; K/M/B suffix above that
  ($50.5K, $1.2M) — one decimal, `.0` dropped
- Percent change with `+`/`-` prefix
- All amounts rounded to whole dollars (no cents)

## Net worth calculation

Net worth = sum of `balance` across all non-closed, non-deleted accounts
(on-budget and tracking). YNAB stores liabilities as negative balances, so a
straight sum gives the correct number.

Monthly delta = sum of all non-deleted transaction `amount` values since the
same calendar day one month ago (clamped to the last day of the prior month
when today doesn't exist there — e.g. Mar 31 → Feb 28/29). Internal transfers
cancel out (matching +/- entries).

## API details

**Auth:** `Authorization: Bearer <token>` with a personal access token.

**Rate limit:** 200 requests per hour (rolling window). A daily cron with
30-minute cache uses at most 2 requests per invocation — well within budget.

**Endpoints:**
- `GET /v1/budgets/{budget_id}/accounts` — all account balances (milliunits)
- `GET /v1/budgets/{budget_id}/transactions?since_date=YYYY-MM-DD` —
  transactions since the same calendar day one month ago, for delta calculation

Amounts are in milliunits (1,000 milliunits = $1).

## Keeping data current

### API changes

Authoritative source: https://api.ynab.com

YNAB recently rebranded "budgets" to "plans" in the app UI. The API v1 paths
(`/budgets/`) remain supported. Monitor YNAB developer announcements for any
endpoint deprecation.
