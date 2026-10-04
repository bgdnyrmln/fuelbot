# ⛽ Latvian Fuel Price Telegram Bot (@fuellvbot)

A Telegram bot that tracks fuel prices in Latvia and shows the cheapest station for each fuel type. It can send you the prices every day at a time you choose.

## Features

- **Latest prices on demand** with `/fuel`
- **Daily updates** at a time of your choice with `/fuelscan`
- **Multiple providers:** Circle K, Virsi, Neste and Straujupite
- **Fuel types:** 95, 98, diesel (DD) and diesel+ (DD+), where available
- **Preferred fuels:** pick only the fuels you care about with `/myfuel`
- **Cheapest overall:** see the best price for each fuel across all providers
- **Built-in caching** so the source websites are never spammed
- **Persistent state:** the cache and daily subscriptions survive restarts

## Commands

| Command | Description |
|---|---|
| `/start` | Welcome message and quick guide |
| `/fuel` | Show the latest cached fuel prices |
| `/fuelscan [HH:MM]` | Get prices every day at the given time (default `08:00`). Without an argument, the bot asks for a time |
| `/break` | Stop daily updates |
| `/myfuel` | Choose which fuels appear in `/fuel` and daily messages (tap buttons to toggle) |
| `/cancel` | Cancel the time prompt |
| `/help` | List available commands |

## How caching works

To avoid overloading the fuel sites (and getting an IP banned), the bot follows a few simple rules:

- Each provider is scraped **at most once per `CACHE_TTL`** (default: 1 hour), no matter how many users or scheduled jobs request prices.
- `/fuel` and the daily messages read from the cache. A scrape only happens when a provider's data is older than the TTL.
- A failed scrape counts as an attempt, so it is not retried until the TTL passes. In the meantime, the last known prices are shown and marked as outdated.
- The cache is stored in `cache.json`, so restarting the bot does not trigger new requests.
