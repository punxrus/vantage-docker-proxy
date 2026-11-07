# Vantage

A self-hosted dashboard in the style of [Glance](https://github.com/glanceapp/glance), configured from one YAML file. Glance has a fixed set of widgets. In Vantage, **adding a widget type takes one Python file and one template**, with no changes to core code ([docs/WRITING_A_WIDGET.md](docs/WRITING_A_WIDGET.md)).

- Python 3.12, FastAPI, Jinja2, HTMX. No JavaScript build step.
- The page shell renders immediately. Each widget then loads on its own, with a loading skeleton, a cache, an error state and a "stale" state. One broken widget never breaks the page.
- The config reloads when you save it. Mistakes show up on the dashboard itself.
- Themes use Glance's HSL model: dark, light and your own, picked from the nav bar.
- Single container on `:8281`. Runs as a non-root user and makes no outbound calls except the ones your widgets need.

## Quick start on ZimaOS

```bash
# on the ZimaBoard (192.168.0.5)
mkdir -p /DATA/AppData/vantage && cd /DATA/AppData/vantage
# copy this repo here (git clone, scp, or the ZimaOS Files app)
cp .env.example .env && nano .env        # MYSPEED_PASSWORD, AGENT_TOKEN_10, PUID/PGID
mkdir -p data && chown -R 1000:1000 data # or set PUID/PGID in .env to the owner of ./data
docker compose up -d --build
```

Open **http://192.168.0.5:8281**. Glance can keep running on `:8280` while you compare the two.

To also show stats and containers for **192.168.0.10**, copy `remote/` to that host and run:

```bash
cd remote && echo "AGENT_TOKEN=$(openssl rand -hex 16)" > .env && docker compose up -d
```

Then copy the same token into Vantage's `.env` as `AGENT_TOKEN_10`. This starts the official `glanceapp/agent` (port 27973) and a read-only Docker socket proxy (port 2375).

### Importing as a ZimaOS custom app

ZimaOS can import a compose file: **App Store → Custom install → Import → Docker Compose**. Paste `docker-compose.yml`, then:

1. Change the relative volume paths to absolute ones: `./config` → `/DATA/AppData/vantage/config` (same for `assets` and `data`).
2. ZimaOS can't run `build:`. Build the image once over SSH first (`docker compose build` in `/DATA/AppData/vantage`), and the imported app will use `image: vantage:latest`.
3. Add the variables from `.env` in the app's environment settings, or keep `env_file` and the absolute path.

Running the stack with `docker compose up -d` over SSH is the simpler option. ZimaOS still lists the containers.

## Your pages

`config/vantage.yml` includes one file per page from `config/pages/`: **Home, Horror, Family, Links, Infra, Local**, in the same order and layout as your Glance. [`reference/vantage.yml`](reference/vantage.yml) is the same config as a single file (`python -m app flatten`).

Changes from your `glance.yml` (all agreed in the parity review):

| What | Change |
|---|---|
| MySpeed | The `custom-api` with the password in the YAML became the `myspeed` widget. The password now comes from `.env` |
| Midterm countdown | Dropped (the date has passed) |
| Tides | Glance sent `begin_date=today`, which NOAA rejects. Vantage sends the real date, so the widget works again |
| Services monitor | Added Home Assistant, IT Tools, Excalidraw and MySpeed |
| `to-do` | Now saved on the server (`data/todo/home-todo.json`), so every device sees the same list. Glance kept it per browser |
| Server stats | `.5` reads the host directly. `.10` needs glance-agent on **27973** (Glance pointed at `:8080`, which is IT Tools) |
| Docker | `.5` goes through the bundled read-only proxy. `.10` goes through the proxy in `remote/`. Raw `:2375` is no longer needed |
| Custom templates | Rewritten from Go templates to Jinja (see [custom-api](#custom-api)) |

## Configuration reference

Keys are kebab-case, as in Glance. Unknown keys are reported as errors, so typos don't fail silently.

### Includes and secrets

```yaml
pages:
  - !include pages/home.yml     # relative to this file
  - $include: pages/links.yml   # Glance syntax also works
```

Inside any string, `${NAME}` reads an environment variable (from `.env` with Docker), `${NAME:-default}` falls back to a default, `${secret:name}` reads `/run/secrets/name`, and `\${` is a literal `${`. A missing variable is reported as a config error.

### Top level

```yaml
server:
  base-url: ""            # set if served under a sub-path behind a proxy, e.g. /dash
  timeout: 10s            # default outbound timeout; per widget: `timeout:`
  fetch-concurrency: 16   # max outbound requests at once
  disk-cache: true        # keep widget data in data/cache across restarts

theme:                    # optional; omit it to use the stock dark theme (like Glance)
  background-color: 240 8 9     # "hue saturation lightness"
  primary-color: 43 50 70
  positive-color: 43 50 70
  negative-color: 0 70 70
  contrast-multiplier: 1.0
  text-saturation-multiplier: 1.0
  light: false
  custom-css-file: my.css       # served from assets/ (or a URL)
  disable-picker: false
  presets: {}                   # Glance 0.8 style; same as top-level `themes:`

themes:                   # extra themes in the nav bar picker (saved per browser)
  ocean:
    background-color: 210 30 12
    primary-color: 190 70 60

branding:
  app-name: Vantage
  logo-text: V
  logo-url: /assets/logo.svg
  favicon-url: /assets/favicon.png
  hide-footer: false
  custom-footer: "<a href='...'>...</a>"

auth:                     # off unless users are listed (LAN-only by default)
  users:
    - username: james
      password-hash: $2b$12$...  # docker compose exec vantage python -m app hash-password

default-page: home        # optional; otherwise the first page
```

### Pages

```yaml
pages:
  - name: Home
    slug: home                     # default: from name
    title: Home dashboard          # browser tab title
    width: default                 # default | slim | wide
    center-vertically: false
    hide-desktop-navigation: false
    head-widgets: []               # full-width row above the columns
    columns:                       # 1-3 columns, 1 or 2 of them `full`
      - size: small                # small = 300px, full = the rest
        widgets: [...]
```

On narrow screens (under 1100px) the columns become tabs in a bottom bar.

### Options every widget accepts

| Key | Meaning |
|---|---|
| `type` | Widget type (required) |
| `title`, `title-url` | Header text and link |
| `hide-header`, `css-class` | Hide the header; extra CSS class |
| `cache` | How long data stays fresh, e.g. `30s`, `15m`, `1h`, `1d` |
| `refresh` | Re-fetch in the browser on this interval |
| `timeout` | Outbound timeout for this widget |
| `id` | Stable id (URL and storage key; to-do uses it) |

### Widgets

| Type | Key options |
|---|---|
| `clock` | `hour-format: 12h\|24h`, `timezone`, `timezones: [{timezone, label}]` |
| `calendar` | `first-day-of-week: sunday…saturday` |
| `weather` | `location: City, [Region,] Country` or `latitude`/`longitude`, `units: metric\|imperial`, `hour-format`, `hide-location`, `show-area-name` (Open-Meteo, no key) |
| `search` | `search-engine: google\|duckduckgo\|bing\|brave\|startpage\|kagi\|perplexity\|youtube\|<url with {QUERY}>`, `new-tab`, `autofocus`, `placeholder`, `bangs: [{title, shortcut, url}]`. Press `/` or `s` to focus, Ctrl+Enter flips new-tab |
| `bookmarks` | `groups: [{title, color, same-tab, hide-arrow, links: [{title, url, icon, description, same-tab}]}]` |
| `monitor` | `sites: [{title, url, check-url, icon, allow-insecure, alt-status-codes, timeout, error-url, basic-auth, method}]`, `style: compact`, `show-failing-only`. `url: tcp://host:port` checks a TCP port |
| `rss` | `feeds: [{url, title, limit, headers, hide-categories, hide-description, item-link-prefix, allow-insecure}]`, `style: list\|detailed-list\|horizontal-cards\|horizontal-cards-2`, `limit`, `collapse-after`, `thumbnail-height`, `card-height`, `single-line-titles`, `preserve-order` |
| `videos` | `channels: [id]`, `playlists: [id]`, `style: horizontal-cards\|grid-cards\|vertical-list`, `limit`, `include-shorts`, `video-url-template` (`{VIDEO-ID}`) |
| `hacker-news` | `sort-by: top\|new\|best`, `limit`, `collapse-after`, `extra-sort-by: engagement`, `comments-url-template` |
| `lobsters` | `sort-by: hot\|new`, `tags`, `instance-url`, `limit` |
| `reddit` | `subreddit`, `sort-by`, `top-period`, `search`, `show-thumbnails`, `show-flairs`, `request-url-template` (`{REQUEST-URL}`, for a proxy if Reddit blocks your IP) |
| `releases` | `repositories: [owner/repo \| gitlab:group/project \| dockerhub:ns/image[:tag]]`, `token` (GitHub), `gitlab-token`, `include-prereleases`, `limit`, `show-source-icon` |
| `markets` | `markets: [{symbol, name, symbol-link, chart-link}]`, `sort-by: change\|absolute-change`, `chart-link-template` (`{SYMBOL}`). Prices come from Yahoo Finance's unofficial API, which sometimes rate-limits (HTTP 429). When it does, the last prices stay on screen marked "stale" |
| `docker-containers` | `sock-path: /var/run/docker.sock \| tcp://host:2375`, `hide-by-default`, `running-only`, `sort-by: name\|status`, `auto-icons`, `containers: {name: {name, icon, url, description, hide}}`. Labels: `vantage.*` or `glance.*` → `name`, `icon`, `url`, `description`, `hide`, `same-tab`, `id`, `parent` |
| `server-stats` | `servers: [{type: local\|remote, name, url, token, mountpoints: {path: {name, hide}}, hide-swap, cpu-temp-sensor}]`. Remote servers use [glance-agent](https://github.com/glanceapp/agent) |
| `custom-api` | See below |
| `myspeed` | `url`, `password`, `limit`, `history` |
| `todo` (or `to-do`) | `id`, `placeholder` |
| `html` | `source` (raw HTML) |
| `iframe` | `source`, `height`, `allow` |
| `group` | `widgets: [...]`, shown as tabs |
| `split-column` | `widgets: [...]`, `max-columns: 2` |

Icons: `si:name` ([Simple Icons](https://simpleicons.org)), `mdi:name` ([Material Design Icons](https://pictogrammers.com/library/mdi/)), `di:name[.png]` ([Dashboard Icons](https://github.com/homarr-labs/dashboard-icons)), `sh:name[.png]` ([selfh.st](https://selfh.st/icons/)), a URL, or `/assets/file.png`. Put `auto-invert ` in front of an icon to invert it on the light theme. Your browser loads these icons from jsDelivr.

### custom-api

Calls any URL and renders the result with a Jinja template you write in the YAML. The template runs in a sandbox.

```yaml
- type: custom-api
  title: Tides
  url: https://api.example.com/data
  method: GET                    # POST/PUT/PATCH/DELETE with `body:` and `body-type: json|string`
  headers: { Authorization: "Bearer ${API_TOKEN}" }
  parameters:
    day: "{{ now().strftime('%Y%m%d') }}"   # strings with {{ }} are rendered at fetch time
  subrequests:                   # extra calls, available as subrequests.<name>.json
    other: { url: https://api.example.com/other }
  options: { team: BOS }         # your own values, available as `options`
  allow-insecure: false
  frameless: false
  cache: 1h
  template: |
    <ul class="list list-gap-10">
      {% for p in json.items %}
      <li class="flex justify-between">
        <span class="color-primary">{{ p.name }}</span>
        <span class="color-subdue" {{ p.time | relative_time }}></span>
      </li>
      {% endfor %}
    </ul>
```

Available in the template: `json`, `text`, `status`, `subrequests`, `options`, `now()`, and the filters `relative_time`, `until`, `parse_time("rfc3339"|"unix"|"%Y-%m-%d %H:%M")`, `format_time("%a %I:%M %p")`, `epoch`, `compact_number`, `format_number`, `dig("a.b.0")`, `strip_html`, `domain`. Without a `url`, the widget renders its template on its own (useful for countdowns).

Going from Glance's Go templates:

| Glance | Vantage |
|---|---|
| `{{ range .JSON.Array "games" }} … {{ end }}` | `{% for g in json.games %} … {% endfor %}` |
| `{{ .String "homeTeam.abbrev" }}` | `{{ g.homeTeam.abbrev }}` |
| `{{ .Float "v" \| printf "%.1f" }}` | `{{ "%.1f" \| format(p.v \| float) }}` |
| `{{ if eq .X "Y" }}…{{ else }}…{{ end }}` | `{% if x == "Y" %}…{% else %}…{% endif %}` |
| `{{ .String "t" \| parseTime "rfc3339" \| toRelativeTime }}` | `{{ t \| relative_time }}` |

## Docker socket security

Read access to `/var/run/docker.sock` gives effectively **root on the host**: anything that can talk to the socket can start a privileged container. That's why the default compose file doesn't mount the socket into Vantage. It runs [`tecnativa/docker-socket-proxy`](https://github.com/Tecnativa/docker-socket-proxy) with `CONTAINERS=1, POST=0`, which only allows listing containers. The proxy isn't published on the host, so only Vantage can reach it.

On `.10`, `remote/docker-compose.yml` publishes the proxy on `:2375` for Vantage to read. Anyone on your LAN can then list container names, labels and status, but they can't change anything. If that's too much exposure, bind it to one interface or firewall it to `192.168.0.5`.

If you want the direct socket anyway (one fewer container), see the commented block at the end of `docker-compose.yml`. You'll need `group_add` with the socket's group id, because Vantage doesn't run as root.

## Operations

- **Health:** `GET /healthz` → `{"status":"ok","config":"ok|error","version":…}`. Docker uses it as the healthcheck.
- **Hot reload:** saving any `.yml` in `config/` reloads the dashboard. If the new config is broken, Vantage keeps serving the last good one and shows a red banner listing the problems. Open tabs show a "Config updated" notice.
- **Validate without starting the server:** `docker compose exec vantage python -m app check`
- **Logs:** JSON lines on stdout. Set `LOG_LEVEL=DEBUG` in `.env` to see each request.
- **New built-in widget or code change:** `docker compose up -d --build`. A new plugin in `config/widgets/` needs `docker compose restart vantage`.

## Development

```bash
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
python -m app                                     # http://localhost:8080 (reads ./config, ./.env)
pytest                                            # no network needed; fixtures in tests/fixtures
python tests/record_fixtures.py                   # re-record fixtures when an API changes
```

Layout:

```
app/
  main.py            routes, startup, hot reload
  config.py          YAML, !include, ${ENV}, Pydantic models
  pages.py           config -> pages of widget instances (bad widgets -> error cards)
  registry.py        auto-discovers widgets
  cache.py           TTL cache, stale fallback, backoff, disk persistence
  renderer.py        skeleton / fragment / error / stale rendering
  templating.py      Jinja filters, icons
  themes.py          HSL themes -> CSS variables
  widgets/           one module per widget (base.py = the contract)
  templates/         layout + widgets/<type>.html
  static/            theme.css, vantage.js, htmx (vendored), font
```

### Why Python and not Go

Glance is written in Go, which gives a smaller image (about 20 MB versus about 150 MB here) and lower memory use. Python was chosen because it makes the widget plugin model simple and readable: a new widget is a plain module with a Pydantic options class, with no compile step and no plugin ABI to manage. On a ZimaBoard the size difference doesn't matter much.
