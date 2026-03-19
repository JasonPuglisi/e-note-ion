# parcel.json

Upcoming package delivery from Parcel (parcelapp.net). Logistics — fires
twice daily showing the soonest active delivery.

## Schedule

### `deliveries` — soonest active package

**Cron:** `15 9,15 * * *` — fires at 09:15 and 15:15 daily.
**Hold:** 600 s | **Timeout:** 1800 s | **Priority:** 5 (Logistics)

Private template. Fires in the `:15` slot at hours that don't overlap with
diving (07/12/16). Solo hold budget at both slots: 600 s — well within the
1800 s ceiling. No `refresh_interval` — delivery status doesn't change
minute-to-minute.

To override schedule fields without editing this file:

```toml
[parcel.schedules.deliveries]
cron = "15 8,12,18 * * *"  # check three times daily
hold = 300
timeout = 900
disabled = true             # skip entirely
```

## Configuration

Add the following to your `config.toml`:

```toml
[parcel]
api_key = "your-parcel-api-key"
```

| Key | Required | Description |
|---|---|---|
| `api_key` | Yes | API key from web.parcelapp.net (requires Parcel Premium) |

### Getting an API key

1. Subscribe to [Parcel Premium](https://parcelapp.net/) ($4.99/year)
2. Sign in at [web.parcelapp.net](https://web.parcelapp.net)
3. Generate an API key in your account settings
4. Copy the key into your `config.toml`

## Display format

The template shows the soonest active delivery with a carrier-colored header:

```
[X] ON THE WAY
PACKAGE NAME
TOMORROW
```

### Carrier color mapping

The header color square reflects the shipping carrier's brand:

| Carrier | Code(s) | Color |
|---|---|---|
| USPS | `usps` | `[B]` blue |
| UPS | `ups` | `[O]` orange |
| FedEx | `fedex` | `[V]` violet |
| DHL | `dhl` | `[Y]` yellow |
| Amazon | `amzl*` (prefix) | `[B]` blue |
| OnTrac | `ontrac`, `laser` | `[B]` blue |
| All others | — | `[O]` orange (Parcel default) |

### Detail line

| Condition | Display |
|---|---|
| Out for delivery (status 4) | `OUT FOR DELIVERY` |
| Expected today | `TODAY` |
| Expected tomorrow | `TOMORROW` |
| Expected in N days | `IN N DAYS` |
| No expected date | (blank) |

### Selection logic

When multiple deliveries are active, the soonest `date_expected` is shown.
Ties are broken alphabetically by package name. No active deliveries → the
template is silently skipped.

## API details

**Endpoint:** `GET https://api.parcel.app/external/deliveries/?filter_mode=active`
**Auth:** `api-key` header
**Rate limit:** 20 requests/hour (server-cached responses)

Active status codes used: 2 (in transit), 3 (pickup awaiting), 4 (out for
delivery), 8 (information received).

## Keeping data current

### Carrier codes

Authoritative source: https://api.parcel.app/external/supported_carriers.json

The carrier color mapping in `integrations/parcel.py` covers major US carriers.
If a new carrier becomes common (or an existing one is renamed), update the
`_CARRIER_COLORS` dict and this table.

### API changes

Parcel does not maintain a public changelog. Monitor the API response format
periodically — the integration parses `deliveries[].status_code`,
`date_expected`, `carrier_code`, and `description` fields.
