# 🍽 Mampfplan

Kleine Familien-Webapp für **Wochen-Essensplan** und **Einkaufsliste** – mobil optimiert, ohne Login, per geheimem Link teilbar.

## Funktionen

- **Wochenplan** mit mehreren Rezepten *oder* Notizen pro Tag (z. B. „Wir gehen essen“), Reihenfolge per ▲▼ änderbar
- **Rezepte**: selbst anlegen (Zutaten + Schritte), bearbeiten, löschen
- **Rezeptsuche** in öffentlichen Datenbanken: **Chefkoch** (deutsch, inoffizielle API), **TheMealDB** (englisch), optional **Spoonacular** (englisch, API-Key nötig). Treffer werden als eigene, editierbare Kopie übernommen. **Rezeptbilder lädt nur der Server** (einmalig, danach aus dem Cache): Die Browser der Familie fragen nie direkt bei Chefkoch & Co. an.
- **Rezept aus Link** (Rezepte → „🔗 Aus Link“): Link zu einem Instagram-Post, einem Foodblog oder einer HelloFresh-Rezeptseite einfügen – daraus entsteht ein Vorschlag mit Titel, Portionen, Zutaten, Anleitung und Bild, den du vor dem Speichern beliebig anpasst. Der Link zur Quelle steht immer als letzte Zeile der Zubereitung (anklickbar) und wird beim Speichern und Bearbeiten wieder ergänzt, falls er gelöscht wurde. Erkannt werden Seiten mit Rezept-Markup (schema.org, z. B. HelloFresh, WordPress-Rezeptplugins) und Fließtext mit Abschnitten wie „Zutaten“ / „Zubereitung“ (auch englisch, mit Emojis, Aufzählungszeichen oder Zutaten in einer Zeile). Bei Instagram wird die Beschreibung des Posts ausgewertet. Liefert Instagram sie nicht aus (das entscheidet Instagram, z. B. für Server in Rechenzentren), fragt die App nach dem Text: Beschreibung kopieren, einfügen, fertig.
- **Einkaufsliste**: Zutaten eines (eingeplanten) Rezepts per Klick hinzufügen, Artikel frei eintippen („2 l Milch“) oder aus dem **Artikelkatalog** (~270 Lebensmittel-, Haushalts- und Hygieneartikel) suchen. Abhaken verschiebt Artikel nach „Gekauft“. Gleiche Artikel werden zusammengeführt (500 g + 250 g Mehl = 750 g). Zutatennamen werden bereinigt („Karotte(n)“ → „Karotte“) und per Katalog einer Kategorie zugeordnet. Artikel und Kategorien lassen sich per **Drag & Drop** am Griff ⠿ umordnen – z. B. passend zum Rundgang im Supermarkt. Unter **„↕ Laden“** bringst du alle Kategorien – auch gerade leere – einmal in die Reihenfolge deines Supermarkts (z. B. Getränke, Obst & Gemüse, Snacks …); eine in der Liste verschobene Kategorie wird vor ihrem neuen Nachbarn eingefügt und bleibt dauerhaft dort. Falsch einsortierte Artikel zieht man einfach **in eine andere Kategorie** (beim Ziehen erscheinen auch leere Kategorien als Ablagefläche); der Haushalt merkt sich die Korrektur für das nächste Mal.
- **Rezept → Einkaufsliste**: In der Rezeptansicht ist standardmäßig nichts angehakt – nur ankreuzen, was wirklich fehlt (oder „Alle wählen“)
- **Verlauf & Statistik**: Änderungsverlauf (wer hat was wann geändert), meistgekochte Rezepte, Kochverlauf
- **Wer war's?** In der Einkaufsliste steht neben jedem Artikel das Kürzel des Profils, das ihn eingetragen hat – bei „Gekauft“ das Kürzel dessen, der ihn in den Wagen gelegt hat (bei gleichem Anfangsbuchstaben die ersten zwei Buchstaben).
- **Rückgängig** für jede Aktion (Button oben rechts), mehrfach hintereinander
- **Teilen**: Wer den Link kennt, kann Plan und Liste sehen und ändern. Kein Passwort, kein Benutzerkonto – beim ersten Öffnen wählt man nur sein Profil (für Verlauf und Kürzel).
- **Mehrere Haushalte**: Jeder Haushalt hat eigene Rezepte, Wochenplan, Einkaufsliste und einen eigenen geheimen Link. Der beim ersten Start angelegte Haushalt verwaltet die übrigen (unter „Mehr → Haushalte verwalten“); in jedem Haushalt legt das erste Profil weitere Profile an.

## Starten mit Docker

```bash
cp .env.example .env      # Namen anpassen
docker compose up -d --build
docker compose exec mampfplan python -m app.cli link
```

Der letzte Befehl zeigt die **Links aller Haushalte** (`http://…/s/<geheimes-token>`). Den Link an die Familie schicken und als Lesezeichen / „Zum Startbildschirm hinzufügen“ speichern. Beim ersten Start steht er auch im Log (`docker compose logs mampfplan`).

### Haushalte und Profile

- Beim **ersten Start** entsteht der Verwaltungs-Haushalt mit den Profilen aus `MEMBERS`. Danach werden Profile nur noch in der App verwaltet (`MEMBERS` wirkt nicht mehr).
- **Weitere Haushalte** legst du im Verwaltungs-Haushalt unter „Mehr → Haushalte verwalten“ an: Name des Haushalts plus erstes Profil. Den angezeigten Link schickst du an den neuen Haushalt. Dort sieht man nur die eigenen Daten.
- **Weitere Profile** legt in jedem Haushalt das erste Profil unter „Mehr → Profile“ an. Dort kann es Profile auch **umbenennen** (✏️) – Kürzel in der Einkaufsliste, Verlauf und „Rezept von …“ ziehen mit – und **löschen**. Das eigene Profil lässt sich nicht löschen; bereits Eingetragenes behält den alten Namen.
- **Haushalte löschen** kann nur der Verwaltungs-Haushalt („Haushalt löschen …“ in der Verwaltung, Name zur Bestätigung eintippen). Das entfernt alle Rezepte, den Wochenplan, die Einkaufsliste, den Verlauf und die Profile des Haushalts **endgültig**. Der Verwaltungs-Haushalt selbst kann nicht gelöscht werden.
- **Link erneuern**: Das erste Profil kann den Link des eigenen Haushalts erneuern, der Verwaltungs-Haushalt den jedes Haushalts. Auf der Kommandozeile: `python -m app.cli rotate <ID>` (IDs zeigt `python -m app.cli link`).
- Ein Gerät kann mehrere Haushalte nutzen; das gewählte Profil merkt sich die App je Haushalt.

> Ohne Login sind die Profil-Rechte eine **Bedienungs-, keine Sicherheitsgrenze**: Wer den Link eines Haushalts hat, kann jedes Profil dieses Haushalts wählen. Die Grenze zwischen Haushalten ist der jeweilige geheime Link.

Die Daten liegen in einer SQLite-Datei im Docker-Volume `mampfplan-data` (Backup: Volume sichern oder statt des Volumes `./data:/data` einbinden und vorher `chown 10001 data` ausführen).

### Produktivbetrieb mit HTTPS (Caddy + certbot)

`docker-compose.caddy.yml` stellt Caddy als HTTPS-Reverse-Proxy auf Port **8443** vor die App. Die App selbst wird dann nicht mehr auf dem Host veröffentlicht, sondern ist nur über Caddy erreichbar. Caddy nutzt die Zertifikate des auf dem Server vorhandenen **certbot** (read-only eingebunden) und holt selbst keine.

1. DNS-Eintrag für die Domain anlegen und sie in das certbot-Zertifikat aufnehmen (`certbot certificates` zeigt Name und enthaltene Domains).
2. In der `.env` setzen (Beispielwerte, siehe `.env.example`):
   ```
   DOMAIN=mampf.example.org
   CERT_NAME=example.org            # nur nötig, wenn der certbot-Ordnername abweicht
   PUBLIC_URL=https://mampf.example.org:8443
   SECURE_COOKIES=true
   COMPOSE_FILE=docker-compose.yml:docker-compose.caddy.yml
   ```
3. `docker compose up -d --build`, Port 8443/tcp in der Firewall freigeben.
4. **Nach jeder Zertifikatserneuerung** muss Caddy die Dateien neu laden. Dafür ein certbot-Deploy-Hook, z. B. `/etc/letsencrypt/renewal-hooks/deploy/mampfplan-caddy.sh` (ausführbar):
   ```sh
   #!/bin/sh
   docker exec mampfplan-caddy caddy reload --config /etc/caddy/Caddyfile
   ```

Zugriffslogs sind in Caddy standardmäßig aus – der geheime Link steht also in keiner Logdatei dieser Komponente.

### Weitere Dienste über denselben Caddy

Caddy lädt zusätzlich alle Dateien `deploy.local/sites/*.caddy`. Dieser Ordner ist **nicht im Repository** (`.gitignore`) – serverspezifische Weiterleitungen mit internen Adressen bleiben so auf dem Server. Beispiel `deploy.local/sites/beispiel.caddy`:

```
dienst.example.org:8123 {
	tls /certs/live/{$CERT_NAME}/fullchain.pem /certs/live/{$CERT_NAME}/privkey.pem
	reverse_proxy 192.168.1.10:8123
}
```

Weitere Ports veröffentlichst du in einer ebenfalls ignorierten `docker-compose.local.yml` und nimmst sie in `COMPOSE_FILE` auf (`docker-compose.yml:docker-compose.caddy.yml:docker-compose.local.yml`):

```yaml
services:
  caddy:
    ports:
      - "8123:8123"
```

Die Domain muss im certbot-Zertifikat enthalten sein; nach Änderungen `docker compose up -d` (neue Ports) bzw. `docker compose exec caddy caddy reload --config /etc/caddy/Caddyfile`.

### Offline im Supermarkt & Display an beim Kochen

- **Offline abhaken:** Die Einkaufsliste (und alle Seiten, die du schon einmal geöffnet hast) steht auch ohne Internet zur Verfügung. Abhaken und Umsortieren funktionieren offline weiter: Die Änderungen werden auf dem Handy gemerkt (Banner „📴 …“) und automatisch übertragen, sobald wieder Verbindung besteht. Wurde derselbe Artikel inzwischen von jemand anderem geändert, gilt der zuletzt gesendete Stand. Hinzufügen, Löschen und Rezeptsuche brauchen Verbindung.
- **Display bleibt an:** Solange eine Rezeptseite offen ist, schaltet das Handy das Display nicht aus.
- **Voraussetzung: HTTPS.** Beides nutzt Browser-Funktionen (Service Worker, Screen Wake Lock), die nur auf `https://…` oder `localhost` erlaubt sind – **nicht** über `http://192.168.x.x`. Betreibe die App deshalb hinter einem Reverse Proxy mit Zertifikat (Nginx Proxy Manager, Caddy, Traefik, Synology-Reverse-Proxy …) und ruf sie darüber auf.
- **Einmal vorher öffnen:** Die Liste muss auf dem Handy mindestens einmal online geladen worden sein (die App aktualisiert sie bei jedem Besuch im Hintergrund). Am besten die App zum Startbildschirm hinzufügen.

### Essensplan als JSON (z. B. für Home Assistant)

`GET /s/<token>/api/plan?days=7` liefert den Plan ab heute für `days` Tage (1–14, Standard 7). Die Schnittstelle ist nur lesend und braucht kein Profil; sie zeigt nur den Haushalt, zu dem das Token gehört. Ein falsches Token ergibt 404, ein ungültiges `days` 422.

```json
{
  "today": "2026-10-21",
  "days": [
    {"date": "2026-10-21", "weekday": "Mittwoch", "entries": [
      {"kind": "recipe", "title": "Spaghetti Carbonara", "cooked": false},
      {"kind": "note", "title": "Reste essen", "cooked": false}
    ]},
    {"date": "2026-10-22", "weekday": "Donnerstag", "entries": []}
  ]
}
```

Die URL enthält das Token und ist damit der Zugang zum ganzen Haushalt. Leg sie in Home Assistant deshalb in der `secrets.yaml` ab und nicht direkt in der Konfiguration:

```yaml
# secrets.yaml
mampfplan_plan_url: https://mampf.example.org/s/<token>/api/plan?days=7

# configuration.yaml
rest:
  - resource: !secret mampfplan_plan_url
    scan_interval: 900
    sensor:
      - name: Essen heute
        value_template: "{{ value_json.days[0].entries | map(attribute='title') | join(', ') or 'nichts geplant' }}"
        json_attributes:
          - days
```

### Einstellungen (`.env`)

| Variable | Bedeutung |
| --- | --- |
| `MEMBERS` | Profile des ersten Haushalts, kommagetrennt – **nur beim allerersten Start**; danach in der App verwalten |
| `SPACE_NAME` | Name des ersten Haushalts (nur beim allerersten Start) |
| `SPACE_TOKEN` | Optional eigenes Token (mind. 22 Zeichen). Leer = wird sicher zufällig erzeugt |
| `PUBLIC_URL` | Adresse, unter der die App erreichbar ist (nur für die Anzeige des Teilen-Links) |
| `SECURE_COOKIES` | `true`, wenn die App hinter HTTPS läuft |
| `SPOONACULAR_API_KEY` | Optional: schaltet Spoonacular als weitere Quelle frei |
| `IMAGE_CACHE_MAX_MB` | Obergrenze des Bild-Caches in MB (Standard 300; die ältesten Bilder werden zuerst gelöscht) |
| `IMAGE_CACHE_DIR` | Ablageort des Bild-Caches (Standard: Ordner `imagecache` neben der Datenbank, also im Daten-Volume) |
| `TIMEZONE` | Standard `Europe/Berlin` |

> **Wichtig:** Betreibe die App im Internet nur hinter HTTPS (Reverse Proxy, z. B. Nginx Proxy Manager, Traefik, Caddy) und setze dann `SECURE_COOKIES=true`. Der Link ist das einzige „Passwort“ – nicht öffentlich posten. Ist er in falsche Hände geraten, erzeugt `docker compose exec mampfplan python -m app.cli rotate` sofort einen neuen Link; der alte wird ungültig, alle Daten bleiben erhalten.

## Lokal entwickeln

```bash
python -m venv .venv
.venv\Scripts\activate            # Linux/macOS: source .venv/bin/activate
pip install -r requirements-dev.txt
uvicorn app.main:app --reload
pytest
```

## Aufbau

```
app/
  main.py            App, Middleware (CSRF-Cookie, Security-Header), Fehlerseiten
  web.py             Zugriff per Token + Profil, CSRF-Prüfung, Template-Helfer
  models.py          Datenbank-Tabellen (SQLAlchemy)
  routers/           plan, recipes, search, shopping, misc (Verlauf, Statistik, Undo)
  services/
    history.py       Änderungsverlauf + Rückgängig (speichert Umkehr-Operationen)
    ingredients.py   "200 g Mehl" parsen/formatieren
    shopping.py      Artikel hinzufügen/zusammenführen, Kategorie raten
    stats.py         Statistik-Abfragen
  recipe_sources/    Chefkoch, TheMealDB, Spoonacular (gemeinsames Interface)
  converter/         Rezept aus Link: fetch.py (abgesicherter Abruf), html.py (Meta, JSON-LD, Text),
                     text.py (Zutaten/Anleitung aus Fließtext erkennen)
  templates/, static/   Jinja2 + HTMX (lokal eingebunden, kein CDN)
seed/catalog.json    Artikelkatalog – einfach erweiterbar
tests/               pytest
```

Eine **neue Rezeptquelle** hinzuzufügen heißt: Klasse von `RecipeSource` ableiten (`_search`, `_get`) und in `recipe_sources/__init__.py` eintragen.

**Artikelkatalog erweitern:** Einträge in `seed/catalog.json` ergänzen und die App neu starten – fehlende Artikel werden beim Start nachgetragen.

## Mitgelieferte Bibliotheken

Lokal eingebunden (kein CDN, wegen der Content-Security-Policy), unverändert von jsDelivr:

- [htmx](https://htmx.org) 2.0.4 – BSD-2-Clause
- [SortableJS](https://sortablejs.github.io/Sortable/) 1.15.6 – MIT

## Sicherheit (OWASP)

- **Zugriffskontrolle:** unerratbares 256-Bit-Token im URL-Pfad; alle Daten sind strikt dem Space zugeordnet, fremde IDs liefern 404
- **Injection:** ausschließlich SQLAlchemy mit gebundenen Parametern; LIKE-Wildcards werden escaped
- **XSS:** Jinja2-Autoescaping, kein Inline-JavaScript/-CSS, strenge Content-Security-Policy; externe URLs nur `https`
- **CSRF:** Double-Submit-Token (Header für HTMX, Feld für Formulare) plus `SameSite=Lax`
- **SSRF:** Rezeptsuche und Bild-Cache nutzen nur fest hinterlegte Hosts, IDs werden per Regex validiert, Timeouts gesetzt. Der Rezept-Konverter ruft vom Nutzer eingegebene Links ab und ist deshalb eigens abgesichert: nur `https` auf Port 443, keine Zugangsdaten in der URL; der Hostname muss ausschließlich auf öffentliche IP-Adressen zeigen (kein Heimnetz, VPN, Docker-Netz, localhost, Cloud-Metadaten); nach dem Verbindungsaufbau wird die tatsächlich verbundene Adresse erneut geprüft (DNS-Rebinding); jede Weiterleitung wird einzeln geprüft (max. 5); Größenlimit 6 MB (Bilder 3 MB), kein Proxy aus Umgebungsvariablen, nur HTML bzw. echte Bilddateien. Der Bild-Cache lädt ausschließlich per `https` von fest hinterlegten Bildservern (Chefkoch, TheMealDB, Spoonacular), folgt keinen Weiterleitungen, begrenzt die Größe und prüft den Dateiinhalt (nur JPEG/PNG/GIF/WebP, nie SVG)
- **Bilder:** Die Content-Security-Policy erlaubt Bilder nur von der App selbst (`img-src 'self' data:`) – ein Bild von einem fremden Server würde der Browser gar nicht laden
- **Security-Header:** CSP, `X-Frame-Options`, `nosniff`, `Referrer-Policy: no-referrer`, `Cache-Control: no-store`, `X-Robots-Tag`
- **Hardening:** Container läuft als Nicht-Root, `read_only`, ohne Capabilities; keine API-Docs; Zugriffslog aus (Token steht im Pfad)
- **Eingaben:** serverseitige Längenlimits und Typprüfung

## Bekannte Grenzen

- Selbst angelegte Rezepte haben (noch) kein Bild; ein Foto-Upload ließe sich nachrüsten. Rezepte aus dem Konverter übernehmen das Bild der Quelle (einmalig vom Server geladen und im Bild-Cache abgelegt).
- Der **Rezept-Konverter** erkennt Fließtext mit einfachen Regeln – das Ergebnis ist immer ein Vorschlag. Ob Instagram die Beschreibung eines Posts ohne Anmeldung herausgibt, entscheidet Instagram; sonst hilft das Einfügen des Textes.
- Die **Chefkoch-Schnittstelle ist inoffiziell** und kann sich jederzeit ändern oder wegfallen. Dann ist nur dieser Tab betroffen; eigene Rezepte und TheMealDB laufen weiter.
- Offline gemerkte Änderungen liegen nur auf dem jeweiligen Handy (Browser-Speicher). Löscht man die Website-Daten, bevor sie übertragen wurden, gehen sie verloren.
- „Rückgängig“ nimmt immer die **letzte** Änderung zurück – auch wenn sie vom anderen Familienmitglied stammt (der Button zeigt, was es ist).
- Es gibt nur eine minimale automatische Schema-Anpassung (`app/db.py: migrate`). Vor größeren Updates bitte die SQLite-Datei sichern.
- Zum Installieren auf dem iPhone-Startbildschirm wird ein SVG-Icon genutzt; ein PNG-Icon wäre noch schöner.
