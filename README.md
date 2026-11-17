# 🍽 Mampfplan

A small family web app for a **weekly meal plan** and a **shopping list**: built for phones, no login, shared through a secret link.

## Features

- **Meal plan** with several recipes *or* notes per day (e.g. "Eating out"); reorder them with ▲▼.
- **Recipes**: create your own (ingredients + steps), edit, delete.
- **Change the servings** in the recipe view with − / +: the ingredients are scaled right away (400 g flour for 4 becomes 200 g for 2) and rounded the way you'd measure (whole grams, quarter spoons, half eggs). Recipes without a servings count are treated as serving 4. Adding ingredients to the shopping list uses the scaled amounts.
- **Recipe search** in public databases: **Chefkoch** (German, unofficial API), **TheMealDB** (English), optionally **Spoonacular** (English, API key required). Results are saved as your own editable copy. **Only the server downloads recipe images** (once, then from its cache): the family's browsers never contact Chefkoch & co. directly.
- **Recipe from link** (Recipes → "🔗 From link"): paste a link to an Instagram post, a TikTok video (short links like `vm.tiktok.com/…` work too), a food blog or a HelloFresh recipe page. The app turns it into a draft with title, servings, ingredients, steps and image, which you can edit freely before saving. The source link is always the last line of the steps (clickable) and is added back on save and edit if it was deleted. The converter understands pages with recipe markup (schema.org, e.g. HelloFresh and WordPress recipe plugins) and plain text with sections like "Ingredients" / "Instructions" (in German, English, French, Spanish and Italian, with emojis, bullet points or ingredients on a single line). Amounts written after the name, as on GialloZafferano („Spaghetti 320 g“), are moved to the front so the shopping list can add them up. For Instagram and TikTok it reads the post or video caption (for TikTok including the thumbnail). If the platform doesn't hand out the caption (the platform decides, e.g. for servers in data centers), the app asks for the text: copy the caption, paste it, done.
- **US measurements are converted automatically** (recipe from link and online search): cups to grams (dry ingredients via a density table, e.g. 1 cup flour ≈ 120 g, sugar ≈ 200 g, butter ≈ 227 g) or ml (liquids and anything unknown), oz/lb/sticks to g, fl oz/pint/quart to ml, tbsp/tsp to the spoon units of the chosen language; in the steps also °F → °C and inch → cm. If the author already wrote the metric amount ("1 cup (125 g) flour"), that amount is used. The draft lists every conversion so you can check it.
- **Shopping list**: add the ingredients of a (planned) recipe with one click, type items freely ("2 l milk") or search the **item catalog** (~270 groceries, household and toiletry items). Ticking an item moves it to "Bought". Identical items are merged (500 g + 250 g flour = 750 g). Ingredient names are cleaned up ("Karotte(n)" → "Karotte") and sorted into a category via the catalog. Items and categories can be reordered by **drag & drop** on the ⠿ handle, e.g. to match your route through the supermarket. Under **"↕ Store"** you put all categories, including currently empty ones, into the order of your supermarket once (e.g. drinks, fruit & vegetables, snacks …); a category moved in the list is placed before its new neighbor and stays there. Drag a misfiled item **into another category** (empty categories show up as drop targets while dragging); the household remembers the correction for next time.
- **Recipe → shopping list**: in the recipe view nothing is ticked by default; tick only what you're actually missing (or "Select all").
- **One shopping list per store**: under "More → Shopping lists" you add lists for Lidl, Aldi Süd/Nord, dm, Netto, Edeka, Rewe, Kaufland, Penny, Rossmann or any name of your own, rename and delete them (their items move to another list) and set the order in which they appear under "Shopping" (drag on ⠿ or ▲/▼). Known stores show their favicon as logo; **only the server fetches it** (once, then from its cache), otherwise a colored tile is shown. With a single list everything lands there; with several you pick the list when adding items (also in the catalog, the recipe view and the 🛒 button of the meal plan). Every list has its own categories and its own store order. Move an item to another store with ⇄ (or drag it there); it lands directly in its category, identical items are merged. Each list can be collapsed and expanded; the phone remembers which ones are collapsed.
- **Meal plan**: in the current week **today is always at the top**, followed by the rest of the week; days already past move to the end. Move planned recipes and notes to another day via 📅 (any day of the week or a free date) or by dragging them on ⠿. If something is already planned there, the entry is added; from there you can move the previous one on to another day.
- **History & statistics**: change history (who changed what and when), most-cooked recipes, cooking history.
- **Who added it?** Next to each item in the shopping list is the initial of the profile that added it; under "Bought", the initial of whoever put it in the cart (the first two letters if initials collide).
- **Undo** for every action (button at the top right), several times in a row.
- **Sharing**: anyone with the link can view and change the plan and the list. No password, no user account; on first visit you just pick your profile (for the history and initials).
- **Multiple households**: each household has its own recipes, meal plan, shopping list and its own secret link. The household created on first start manages the others ("More → Manage households"); in every household the first profile creates further profiles.
- **Five languages**: German, English, French, Spanish and Italian, chosen per device (see [Languages](#languages)).

## Running with Docker

```bash
cp .env.example .env      # adjust the names
docker compose up -d --build
docker compose exec mampfplan python -m app.cli link
```

The last command prints the **links of all households** (`http://…/s/<secret-token>`). Send the link to your family and save it as a bookmark / "Add to Home Screen". On first start it is also printed in the log (`docker compose logs mampfplan`).

### Households and profiles

- On **first start** the admin household is created with the profiles from `MEMBERS`. After that, profiles are managed only in the app (`MEMBERS` no longer has any effect).
- **More households** are created in the admin household under "More → Manage households": household name plus first profile. Send the link shown to the new household. They only see their own data.
- **More profiles** are created in each household by the first profile under "More → Profiles". There it can also **rename** profiles (✏️; the initials in the shopping list, the history and "Recipe by …" follow along) and **delete** them. Your own profile can't be deleted; existing entries keep the old name.
- **Deleting households** is only possible from the admin household ("Delete household …" in the household management, type the name to confirm). This **permanently** removes all recipes, the meal plan, the shopping list, the history and the profiles of that household. The admin household itself can't be deleted.
- **Renewing a link**: the first profile can renew its own household's link, the admin household can renew any household's link. On the command line: `python -m app.cli rotate <ID>` (`python -m app.cli link` shows the IDs).
- One device can use several households; the app remembers the chosen profile per household.

> Without a login, profile permissions are a **usability boundary, not a security boundary**: anyone with a household's link can pick any profile of that household. The boundary between households is each household's secret link.

The data lives in a SQLite file in the Docker volume `mampfplan-data` (backup: back up the volume, or mount `./data:/data` instead of the volume and run `chown 10001 data` first).

### Production with HTTPS (Caddy + certbot)

`docker-compose.caddy.yml` puts Caddy in front of the app as an HTTPS reverse proxy on port **8443**. The app itself is then no longer published on the host and is only reachable through Caddy. Caddy uses the certificates of the **certbot** already installed on the server (mounted read-only) and doesn't obtain any itself.

1. Create a DNS record for the domain and add it to the certbot certificate (`certbot certificates` shows the name and the domains it contains).
2. Set in `.env` (example values, see `.env.example`):
   ```
   DOMAIN=mampf.example.org
   CERT_NAME=example.org            # only needed if the certbot folder name differs
   PUBLIC_URL=https://mampf.example.org:8443
   SECURE_COOKIES=true
   COMPOSE_FILE=docker-compose.yml:docker-compose.caddy.yml
   ```
3. Run `docker compose up -d --build` and open port 8443/tcp in the firewall.
4. **After every certificate renewal** Caddy has to reload the files. Add a certbot deploy hook for that, e.g. `/etc/letsencrypt/renewal-hooks/deploy/mampfplan-caddy.sh` (executable):
   ```sh
   #!/bin/sh
   docker exec mampfplan-caddy caddy reload --config /etc/caddy/Caddyfile
   ```

Access logs are off in Caddy by default, so the secret link doesn't end up in any log file of this component.

### More services behind the same Caddy

Caddy also loads every file `deploy.local/sites/*.caddy`. This folder is **not in the repository** (`.gitignore`), so server-specific proxies with internal addresses stay on the server. Example `deploy.local/sites/example.caddy`:

```
service.example.org:8123 {
	tls /certs/live/{$CERT_NAME}/fullchain.pem /certs/live/{$CERT_NAME}/privkey.pem
	reverse_proxy 192.168.1.10:8123
}
```

Publish additional ports in a `docker-compose.local.yml` (also ignored) and add it to `COMPOSE_FILE` (`docker-compose.yml:docker-compose.caddy.yml:docker-compose.local.yml`):

```yaml
services:
  caddy:
    ports:
      - "8123:8123"
```

The domain must be included in the certbot certificate. After changes run `docker compose up -d` (new ports) or `docker compose exec caddy caddy reload --config /etc/caddy/Caddyfile`.

### Offline in the supermarket & screen on while cooking

- **Tick items offline:** the shopping list (and every page you've opened before) is available without internet. Ticking and reordering keep working offline: changes are kept on the phone (banner "📴 …") and sent automatically once you're back online. If someone else changed the same item in the meantime, the last change sent wins. Adding, deleting and recipe search need a connection.
- **Screen stays on:** while a recipe page is open, the phone doesn't turn off its screen.
- **Requires HTTPS.** Both use browser features (service worker, Screen Wake Lock) that are only allowed on `https://…` or `localhost`, **not** over `http://192.168.x.x`. So run the app behind a reverse proxy with a certificate (Nginx Proxy Manager, Caddy, Traefik, Synology reverse proxy …) and open it through that.
- **Open it once beforehand:** the list must have been loaded online on the phone at least once (the app refreshes it in the background on every visit). Best to add the app to your home screen.

### Meal plan as JSON (e.g. for Home Assistant)

`GET /s/<token>/api/plan?days=7` returns the plan from today for `days` days (1–14, default 7). The endpoint is read-only and needs no profile; it only shows the household the token belongs to. A wrong token returns 404, an invalid `days` 422. Weekdays are German by default; `lang=en|fr|es|it` switches them. Response for `?days=7&lang=en`:

```json
{
  "today": "2026-10-21",
  "days": [
    {"date": "2026-10-21", "weekday": "Wednesday", "entries": [
      {"kind": "recipe", "title": "Spaghetti Carbonara", "cooked": false},
      {"kind": "note", "title": "Leftovers", "cooked": false}
    ]},
    {"date": "2026-10-22", "weekday": "Thursday", "entries": []}
  ]
}
```

The URL contains the token and therefore grants access to the whole household. So put it in Home Assistant's `secrets.yaml`, not directly in the configuration:

```yaml
# secrets.yaml
mampfplan_plan_url: https://mampf.example.org/s/<token>/api/plan?days=7&lang=en

# configuration.yaml
rest:
  - resource: !secret mampfplan_plan_url
    scan_interval: 900
    sensor:
      - name: Dinner today
        value_template: "{{ value_json.days[0].entries | map(attribute='title') | join(', ') or 'nothing planned' }}"
        json_attributes:
          - days
```

### Languages

Mampfplan is available in **German, English, French, Spanish and Italian**. The language is set **per device**: on first visit it follows the browser language (German otherwise); you can change it under **"More → Language"**, on the profile picker and on the start page (stored in the cookie `mp_lang`).

- **Translated:** the whole interface, messages, history and undo (retroactively too: everyone sees the history in their own language), categories, the item catalog (suggestions while typing in your own language), dates and decimal separators.
- **Not translated:** whatever you enter yourselves. Recipes, notes and items stay as they were written.
- **The list recognizes categories across languages:** "Milk", "Lait", "Leche" and "Latte" end up in dairy just like "Milch".
- Ingredients also understand French, Spanish and Italian units (cs/cc, pincée, gousse, cda/cdta, pizca, diente, cucchiaio/cucchiaino, pizzico, spicchio …); the recipe converter recognizes French, Spanish and Italian captions (Ingrédients/Préparation, Ingredientes/Preparación, Ingredienti/Procedimento). Converted US spoons are called EL/TL, tbsp/tsp, cs/cc, cda/cdta or cucchiaio/cucchiaino depending on the language.
- Recipe search starts with Chefkoch in German and with TheMealDB in the other languages.
- The JSON endpoint still returns German weekdays by default; `?lang=en|fr|es|it` switches them.

**Adding a language or changing texts:** translations live in `app/i18n/<language>.json` (the key is the German text), catalog names in `seed/catalog.translations.json`. `python -m app.i18n.extract fr` lists the missing texts of a language; a test makes sure every language file is complete.

### Settings (`.env`)

| Variable | Meaning |
| --- | --- |
| `MEMBERS` | Profiles of the first household, comma-separated. **Only on the very first start**; manage them in the app afterwards |
| `SPACE_NAME` | Name of the first household (only on the very first start) |
| `SPACE_TOKEN` | Optional custom token (at least 22 characters). Empty = a secure random one is generated |
| `PUBLIC_URL` | Address where the app is reachable (only used to display the share link) |
| `SECURE_COOKIES` | `true` when the app runs behind HTTPS |
| `SPOONACULAR_API_KEY` | Optional: enables Spoonacular as an additional source |
| `IMAGE_CACHE_MAX_MB` | Size limit of the image cache in MB (default 300; the oldest images are deleted first) |
| `IMAGE_CACHE_DIR` | Location of the image cache (default: folder `imagecache` next to the database, i.e. in the data volume) |
| `TIMEZONE` | Default `Europe/Berlin` |

> **Important:** only run the app on the internet behind HTTPS (reverse proxy, e.g. Nginx Proxy Manager, Traefik, Caddy) and then set `SECURE_COOKIES=true`. The link is the only "password", so don't post it publicly. If it falls into the wrong hands, `docker compose exec mampfplan python -m app.cli rotate` creates a new link right away; the old one stops working and all data is kept.

## Local development

```bash
python -m venv .venv
.venv\Scripts\activate            # Linux/macOS: source .venv/bin/activate
pip install -r requirements-dev.txt
uvicorn app.main:app --reload
pytest
```

## Structure

```
app/
  main.py            app, middleware (CSRF cookie, security headers, language), error pages
  web.py             access via token + profile, CSRF check, template helpers
  models.py          database tables (SQLAlchemy)
  routers/           plan, recipes, search, shopping, api, images, misc (history, statistics, undo)
  services/
    history.py       change history + undo (stores inverse operations)
    ingredients.py   parse/format "200 g flour"
    shopping.py      add/merge items, guess category
    stats.py         statistics queries
    units.py         US measurements → metric (cup, oz, lb, fl oz, °F, inch)
  recipe_sources/    Chefkoch, TheMealDB, Spoonacular (shared interface)
  i18n/              languages: selection, translations (en/fr/es/it.json), dates/numbers, text extraction
  converter/         recipe from link: fetch.py (hardened fetching), html.py (meta, JSON-LD, text),
                     text.py (detect ingredients/steps in plain text)
  templates/, static/   Jinja2 + HTMX (served locally, no CDN)
seed/catalog.json    item catalog, easy to extend
tests/               pytest
```

Adding a **new recipe source** means: subclass `RecipeSource` (`_search`, `_get`) and register it in `recipe_sources/__init__.py`.

**Extending the item catalog:** add entries to `seed/catalog.json` (and their names in other languages to `seed/catalog.translations.json`) and restart the app; missing items are added on startup.

## Bundled libraries

Served locally (no CDN, because of the Content Security Policy), unmodified from jsDelivr:

- [htmx](https://htmx.org) 2.0.4 – BSD-2-Clause
- [SortableJS](https://sortablejs.github.io/Sortable/) 1.15.6 – MIT

## Security (OWASP)

- **Access control:** unguessable 256-bit token in the URL path; all data is strictly bound to its household, foreign IDs return 404
- **Injection:** SQLAlchemy with bound parameters only; LIKE wildcards are escaped
- **XSS:** Jinja2 autoescaping, no inline JavaScript/CSS, strict Content Security Policy; external URLs only `https`
- **CSRF:** double-submit token (header for HTMX, field for forms) plus `SameSite=Lax`; the language switch only redirects to the app's own relative paths
- **SSRF:** recipe search and image cache only use fixed hosts, IDs are validated by regex, timeouts are set. The recipe converter fetches links entered by users and is therefore hardened separately: only `https` on port 443, no credentials in the URL; the hostname must resolve exclusively to public IP addresses (no home network, VPN, Docker network, localhost, cloud metadata); after connecting, the address actually connected to is checked again (DNS rebinding); every redirect is checked on its own (max. 5); size limit 6 MB (images 3 MB), no proxy from environment variables, only HTML or real image files. The image cache downloads only via `https` from fixed image servers (Chefkoch, TheMealDB, Spoonacular), follows no redirects, limits the size and checks the file content (JPEG/PNG/GIF/WebP only, never SVG)
- **Images:** the Content Security Policy only allows images from the app itself (`img-src 'self' data:`); the browser wouldn't even load an image from a foreign server
- **Security headers:** CSP, `X-Frame-Options`, `nosniff`, `Referrer-Policy: no-referrer`, `Cache-Control: no-store`, `X-Robots-Tag`
- **Hardening:** the container runs as non-root, `read_only`, without capabilities; no API docs; access log off (the token is in the path)
- **Input:** server-side length limits and type checks

## Known limitations

- Recipes you create yourself have no image (yet); a photo upload could be added. Recipes from the converter take the source's image (downloaded once by the server and stored in the image cache).
- The **recipe converter** recognizes plain text with simple rules, so the result is always a draft. Whether Instagram/TikTok hand out the caption without a login is up to the platforms; otherwise pasting the text helps. On TikTok the recipe is often only spoken in the video, written in the images of a photo post or in the pinned comment. The app can't read those, so paste the text yourself.
- The **Chefkoch API is unofficial** and may change or disappear at any time. Only that tab would be affected; your own recipes and TheMealDB keep working.
- Changes saved offline only live on that phone (browser storage). If you clear the site data before they've been sent, they're lost.
- "Undo" always reverts the **latest** change, even if another family member made it (the button shows what it is).
- There is only minimal automatic schema migration (`app/db.py: migrate`). Please back up the SQLite file before larger updates.
- An SVG icon is used for installing on the iPhone home screen; a PNG icon would be nicer.
