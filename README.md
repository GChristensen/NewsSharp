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
cp .env.example .env                      # optional: port, data directory, ...
uv run python manage.py serve
```

Open `http://localhost:10000`. The first visit shows a form that creates the administrator account.

`serve` applies migrations and supervises two processes:

| Process | Command | Does |
|---|---|---|
| web | `manage.py web` | API, built frontend, event stream, WebSub callback, image proxy |
| worker | `manage.py worker` | scheduler, feed fetching, story pipeline, icons, text extraction, hub requests, pruning |

The database is the only link between them. Either can be run alone under an external service manager.

## Configuration (`.env`)

| Key | Default | Meaning |
|---|---|---|
| `PORT` / `BIND` | `10000` / `0.0.0.0` | Listen address of the web process |
| `DATA_DIR` | `./data` | Database, secret key, lock file, optional image cache |
| `TRUST_PROXY` | `0` | `1` to honor `X-Forwarded-*` headers behind a reverse proxy |
| `FETCH_INTERVAL_MINUTES` | `60` | Upper bound of the polling interval of a healthy feed |
| `STORIES_PER_FEED` | `2000` | Stories kept per feed (unread stories are never pruned) |
| `WORKER_PROCESSES` | `2` | Size of the worker's process pool |
| `PUSH_LEASE_SECONDS` | `2147483647` | Lease requested from WebSub hubs |
| `IMAGE_CACHE_MB` | `0` | Disk cache for proxied images (0 = off) |
| `LOG_LEVEL` | `info` | |

There is no host name setting. The address given to WebSub hubs is learned from the most recent login
and can be pinned in Preferences → General (administrators).

Behind a reverse proxy, do not buffer `/api/events` and allow long-lived responses.

### When to set `TRUST_PROXY=1`

Set it when a reverse proxy sits in front of NewsSharp and every request goes through it. Leave it at
`0` when browsers connect to NewsSharp's port directly, even if a proxy is also in use.

With `TRUST_PROXY=1`, NewsSharp takes these values from headers the proxy sends instead of from the
connection:

| Header | Used for |
|---|---|
| `X-Forwarded-Proto` | Whether the request is https: the `Secure` flag on the session and CSRF cookies, `upgrade-insecure-requests`, refusing to frame http pages in https mode, and the CSRF origin check |
| `X-Forwarded-Host`, `X-Forwarded-Port` | The address the browser used: the CSRF origin check, and the callback address learned from logins and given to WebSub hubs |
| `X-Forwarded-For` (first entry) | The client address used to throttle login attempts |

- **HTTPS proxy, `TRUST_PROXY=0`:** NewsSharp thinks the page is plain http. Django's CSRF check
  rejects the browser's `https://` origin, so login and every other change fail with a CSRF error.
  Cookies go out without `Secure`, hubs get an `http://` callback, and every client shares one
  throttling bucket: the proxy's address.
- **No proxy, `TRUST_PROXY=1`:** anyone can send these headers. A client can claim any IP address and
  get around login throttling, or claim https and a host of their choosing.
- **Proxy, but the port is also reachable directly:** same as above for whoever bypasses the proxy.
  Keep `BIND=127.0.0.1`, or firewall the port, so that only the proxy can connect.

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
   `cp .env.example .env` there.
2. In `.env`, set `BIND=127.0.0.1` so only nginx can reach the server, set `TRUST_PROXY=1`, and use the
   same `PORT` as `proxy_pass` in `nginx.conf` (10000 in both examples).
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

See [When to set `TRUST_PROXY=1`](#when-to-set-trust_proxy1) for what the option changes and why the
port must not be reachable around the proxy.

## Commands

```sh
uv run python manage.py serve                 # everything
uv run python manage.py web | worker          # one process
uv run python manage.py backup <file>         # online backup of the database
uv run python manage.py createsuperuser       # or changepassword <user>
uv run python manage.py rebuild_counters [feed id]
uv run python manage.py import_newsblur export.json
```

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

## Deviations from the requirements document

- The API layer is plain Django views with hand-written validation (the fallback of decision 25) instead
  of django-ninja, so there is no generated OpenAPI document; the frontend's types are written by hand.
- Split panes and tree drag and drop are implemented directly instead of with `splitpanes` / `vuedraggable`.
  Dragging is disabled while the sidebar is filtered or in Unread / Saved for Later mode.
- Schema additions: `folders.deleted_at` (undo), `feeds.image_url`, `feeds.redirects_in_row`, `jobs.blob`
  (pushed bodies), and `removing`, `lease_seconds`, `requested_at` on push subscriptions.
- Server cache hints (`max-age`, `Retry-After`) shorter than 15 minutes are raised to 15 minutes.
- `Content-Security-Policy: upgrade-insecure-requests` is sent only on https pages; on plain http it
  would upgrade the application's own requests.
- The scope `saved:folder:<id>` was added for folders opened in Saved for Later mode.
- Graceful shutdown of children relies on Ctrl+C reaching the console group on Windows; a supervisor
  killed any other way stops its children without the 10 s grace period.
- Sidebar (R7.1): there is no Saved for Later row (the mode switch at the bottom replaces it; saved tags
  are listed in Saved mode). All Site Stories is shown only with the `show_all_site_stories` preference
  or in Saved mode, where it is labelled All Saved Stories. The feed filter is hidden until the funnel button (or `g`) shows it; the
  `show_feed_filter` preference remembers that.
- The content top bar has no Next unread button (`n` remains). Next story (`j`, the right arrow button)
  past the last story of a feed opens the next feed with unread stories.
- The content top bar holds the read filter, the order and a List style popover (density, previews,
  titles pane, Grid options); the bottom Style popover keeps the story options. The top bar is not tinted
  with the feed color. Unread counts are a neutral dark grey everywhere; the unread dot of a story row
  takes its feed's icon color.
- Stories per row in Grid (`grid_columns`, 1–6, default 4) is also a per-scope view setting; there is no
  automatic column count.
- The content preview (title only / small / medium / large) and the image preview position are per-scope
  view settings too. The List style popover sets them for the open feed or folder; the preferences
  `show_content_preview` and `image_preview` are the defaults.
- Feeds can be muted (the feed menu's Mute / Unmute; `PATCH /api/subscriptions/<id> {muted}`), which the
  requirements drop. A muted feed stays in the tree with a mute icon in place of its unread count. It is
  left out of All Site Stories and folders (their story lists, search, counts, mark-read and the new
  stories bar), of Unread mode, and of next-unread navigation. Opened by itself it lists, counts and marks
  read as usual, and it is still fetched. Saved for Later is unaffected.
- Marking a whole feed or folder read from the story titles header (the Mark read button, its "Mark all
  as read" entry, `Shift+a`) shows an Undo toast for 10 s (`POST /api/mark-read/undo {undo_token}`).
  Undo restores each feed's read date unless it has changed since, and reopens the feed or folder if
  "mark read, then next feed" moved on. All Site Stories, "older than N days" and the sidebar have no undo.
