# NewsSharp

A personal, self-hosted RSS reader, a lightweight reimplementation of NewsBlurMod.
Three panes, a feed/folder tree with drag and drop, feed icon colors, five story layouts, four story
views, unread tracking, Saved for Later, full-text search, text extraction, WebSub push and live updates.

One SQLite file, two Python processes, no Docker, Redis, Celery, MongoDB, Elasticsearch or Node services.

## Requirements

- Python 3.12+ and [uv](https://docs.astral.sh/uv/)
- Node.js 20+ (only to build the frontend)

## Setup

```sh
uv sync                                   # Python dependencies
cd frontend && npm install && npm run build && cd ..
cp config.example.yaml config.yaml       # optional: port, data directory, proxy, ...
uv run python manage.py serve
```

Open `http://localhost:10000`. The first visit shows a form that creates the administrator account.

`serve` applies migrations and supervises two processes:

| Process | Command | Does |
|---|---|---|
| web | `manage.py web` | API, built frontend, event stream, WebSub callback, image proxy |
| worker | `manage.py worker` | scheduler, feed fetching, story pipeline, icons, text extraction, hub requests, pruning |

The database is the only link between them. Either can be run alone under an external service manager.

## Configuration (`config.yaml`)

`config.yaml` in the installation directory is optional; every key has a default. Unknown keys and values
of the wrong type stop the process with an error. Restart `serve` after changing it.

| Key | Default | Meaning |
|---|---|---|
| `port` / `bind` | `8000` / `0.0.0.0` | Listen address of the web process (the example file uses 10000) |
| `data_dir` | `./data` | Database, secret key, lock file, optional image cache |
| `trust_proxy` | `false` | `true` to honor `X-Forwarded-*` headers behind a reverse proxy |
| `fetch_interval_minutes` | `60` | Upper bound of the polling interval of a healthy feed |
| `stories_per_feed` | `500` | Stories kept per feed (unread stories are never pruned; the example file uses 2000) |
| `worker_processes` | `2` | Size of the worker's process pool |
| `push_lease_seconds` | `2147483647` | Lease requested from WebSub hubs |
| `image_cache_mb` | `0` | Disk cache for proxied images (0 = off) |
| `log_level` | `info` | |
| `proxy.url` | none | Outbound proxy for the feeds set to use it (feed context menu → Settings: feed, icon and story text downloads): `http://`, `https://`, `socks5://` or `socks5h://`, credentials as `user:password@` |
| `proxy.verify` | `true` | TLS check of an `https://` proxy: `true` (system CA store), `false` (no check, for a self-signed certificate) or the path of a PEM CA bundle or the proxy's own certificate |

Relative paths (`data_dir`, `proxy.verify`) are relative to the installation directory.

There is no host name setting. The address given to WebSub hubs is learned from the most recent login
and can be pinned in Preferences → General (administrators).

Behind a reverse proxy, do not buffer `/api/events` and allow long-lived responses.

### When to set `trust_proxy: true`

Set it when a reverse proxy sits in front of NewsSharp and every request goes through it. Leave it
`false` when browsers connect to NewsSharp's port directly, even if a proxy is also in use.

With `trust_proxy: true`, NewsSharp takes these values from headers the proxy sends instead of from the
connection:

| Header | Used for |
|---|---|
| `X-Forwarded-Proto` | Whether the request is https: the `Secure` flag on the session and CSRF cookies, `upgrade-insecure-requests`, refusing to frame http pages in https mode, and the CSRF origin check |
| `X-Forwarded-Host`, `X-Forwarded-Port` | The address the browser used: the CSRF origin check, and the callback address learned from logins and given to WebSub hubs |
| `X-Forwarded-For` (first entry) | The client address used to throttle login attempts |

- **HTTPS proxy, `trust_proxy: false`:** NewsSharp thinks the page is plain http. Django's CSRF check
  rejects the browser's `https://` origin, so login and every other change fail with a CSRF error.
  Cookies go out without `Secure`, hubs get an `http://` callback, and every client shares one
  throttling bucket: the proxy's address.
- **No proxy, `trust_proxy: true`:** anyone can send these headers. A client can claim any IP address and
  get around login throttling, or claim https and a host of their choosing.
- **Proxy, but the port is also reachable directly:** same as above for whoever bypasses the proxy.
  Keep `bind: 127.0.0.1`, or firewall the port, so that only the proxy can connect.

The proxy must set all four headers and overwrite `X-Forwarded-For` rather than append to it. Otherwise
its first entry is whatever the client sent. `deploy/nginx.conf` does both.

## Running on a Linux server

NewsSharp serves plain HTTP only. HTTPS comes from a reverse proxy. `deploy/` holds an example of each
piece:

| File | Is |
|---|---|
| `deploy/newssharp.service` | systemd system unit that runs `manage.py serve` as an unprivileged user |
| `deploy/nginx.conf` | nginx site: TLS, HTTP/2, http → https redirect, unbuffered event stream |

1. Put the code in the installation directory (`just deploy`, or a copy) and run `uv sync` and
   `cp config.example.yaml config.yaml` there.
2. In `config.yaml`, set `bind: 127.0.0.1` so only nginx can reach the server, set `trust_proxy: true`, and use the
   same `port` as `proxy_pass` in `nginx.conf` (10000 in both examples).
3. Edit `User`, `Group` and the paths in the unit, then install it:

   ```sh
   sudo cp deploy/newssharp.service /etc/systemd/system/
   sudo systemctl daemon-reload && sudo systemctl enable --now newssharp
   journalctl -u newssharp -f
   ```

4. Edit `server_name`, the ports and the certificate paths in `nginx.conf`, then install it:

   ```sh
   sudo cp deploy/nginx.conf /etc/nginx/conf.d/newssharp.conf
   sudo nginx -t && sudo systemctl reload nginx
   ```

`serve` restarts a crashed web or worker process by itself. systemd restarts `serve` only if `serve`
itself fails. After `just deploy`, run `sudo systemctl restart newssharp`, which also applies migrations.

See [When to set `trust_proxy: true`](#when-to-set-trust_proxy-true) for what the option changes and why the
port must not be reachable around the proxy.

## Commands

```sh
uv run python manage.py serve                 # everything
uv run python manage.py web | worker          # one process
uv run python manage.py backup <file>         # online backup of the database
uv run python manage.py createsuperuser       # or changepassword <user>
uv run python manage.py rebuild_counters [feed id]
uv run python manage.py import_newsblur export.json
uv run python manage.py purge_feeds [--saved] [--folders] [--yes]
```

`purge_feeds` resets a database for testing: it deletes every feed with its stories, read state, icons,
fetch logs, counters, subscriptions and per-feed view settings. `--saved` also deletes all saved stories,
`--folders` all folders; users and preferences are always kept. It asks for confirmation unless `--yes`
is given, and refuses to run while the worker's heartbeat is fresh (stop `serve` first, or pass `--force`).

Users are managed in the Django admin at `/admin/` (Manage → Administration).

## Migrating from NewsBlurMod

1. In the NewsBlurMod repository: `uv run python utils/export_for_reader.py > export.json`
2. Here, on an empty database: `uv run python manage.py import_newsblur export.json`

Users, feeds, subscriptions, the folder tree, custom titles, icons, preferences and per-feed view settings
are migrated. Stories, read state, saved stories and passwords are not: the command prints a one-time
password per user and writes `migration_map.json` (old feed id → new feed id).

## Development

```sh
uv run pytest                    # backend tests
uv run ruff check .
cd frontend
npm run dev                      # Vite dev server, proxies /api to http://127.0.0.1:8000
npm run typecheck && npm test
```

## Layout

```
config/     settings, urls, asgi
accounts/   User model, preference catalog
feeds/      Feed, Story, icons, fetch log, push subscriptions; parsing, sanitizing, FTS helpers
reading/    folders, subscriptions, read state, saved stories, view settings; tree, unread, search, OPML
api/        HTTP views, event bus and stream, image proxy, push callback, job requests
worker/     coordinator, process pool, tasks, pipeline, scheduler, fetcher, WebSub, management commands
frontend/   Vue 3 + Vite + Pinia application (built into frontend/dist)
deploy/     example systemd unit and nginx site
tests/      pytest suite
```

## License

NewsSharp is free software: you can redistribute it and/or modify it under the terms of the GNU
General Public License as published by the Free Software Foundation, either version 3 of the License,
or (at your option) any later version. See [`LICENSE`](LICENSE) for the full text.
