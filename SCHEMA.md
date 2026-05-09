# Structured Output Schema

`x-news-alert` uses a shared agent-friendly envelope for machine-readable output.

Use `--structured` to enable this envelope for JSON-producing commands:

```bash
x-news-alert --structured config
x-news-alert --structured schedule show
x-news-alert --structured fetch blogger elonmusk
```

## Success

```json
{
  "ok": true,
  "schema_version": "1",
  "data": {}
}
```

## Error

```json
{
  "ok": false,
  "schema_version": "1",
  "error": {
    "code": "alert_error",
    "message": "run blogger/list requires an identifier"
  }
}
```

## Common Commands

- `config`: returns the normalized runtime configuration.
- `schedule show`: returns normalized schedule settings.
- `schedule doctor`: returns readiness checks under `data.checks`.
- `fetch blogger <username>`: returns raw tweet data from the configured `twitter` command.
- `fetch list <id>`: returns raw tweet data from the configured `xurl` command.
- `analyze single <file>`: returns one analysis object.
- `analyze batch <file>`: returns a list of analysis objects.

## Tweet Shape

Tweet rows are normalized before deduplication, filtering, and analysis.

```json
{
  "id": "1234567890",
  "text": "tweet body",
  "author": {
    "id": "42",
    "name": "Alice",
    "username": "alice",
    "screenName": "alice"
  },
  "created_at": "2026-05-09T00:00:00Z",
  "createdAt": "2026-05-09T00:00:00Z",
  "metrics": {
    "likes": 10,
    "retweets": 2,
    "replies": 1,
    "quotes": 0,
    "bookmarks": 3,
    "views": 1000
  },
  "public_metrics": {
    "like_count": 10,
    "retweet_count": 2,
    "reply_count": 1,
    "quote_count": 0,
    "bookmark_count": 3,
    "impression_count": 1000
  }
}
```

## Error Codes

Current structured errors use:

- `alert_error`: expected operational failures reported by the CLI.

Future versions may split this into more specific codes such as:

- `not_authenticated`
- `invalid_input`
- `rate_limited`
- `network_error`
- `api_error`
