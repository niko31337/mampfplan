# 🍽 Mampfplan

Kleine Familien-Webapp für **Wochen-Essensplan** und **Einkaufsliste** – mobil optimiert, ohne Login, per geheimem Link teilbar.

## Funktionen

- **Wochenplan** mit mehreren Rezepten *oder* Notizen pro Tag (z. B. „Wir gehen essen“), Reihenfolge per ▲▼ änderbar
- **Rezepte**: selbst anlegen (Zutaten + Schritte), bearbeiten, löschen
- **Rezeptsuche** in öffentlichen Datenbanken: **Chefkoch** (deutsch, inoffizielle API), **TheMealDB** (englisch), optional **Spoonacular** (englisch, API-Key nötig). Treffer werden als eigene, editierbare Kopie übernommen. **Rezeptbilder lädt nur der Server** (einmalig, danach aus dem Cache): Die Browser der Familie fragen nie direkt bei Chefkoch & Co. an.
- **Einkaufsliste**: Zutaten eines (eingeplanten) Rezepts per Klick hinzufügen, Artikel frei eintippen („2 l Milch“) oder aus dem **Artikelkatalog** (~270 Lebensmittel-, Haushalts- und Hygieneartikel) suchen. Abhaken verschiebt Artikel nach „Gekauft“. Gleiche Artikel werden zusammengeführt (500 g + 250 g Mehl = 750 g). Zutatennamen werden bereinigt („Karotte(n)“ → „Karotte“) und per Katalog einer Kategorie zugeordnet. Artikel und Kategorien lassen sich per **Drag & Drop** am Griff ⠿ umordnen – z. B. passend zum Rundgang im Supermarkt.
- **Rezept → Einkaufsliste**: In der Rezeptansicht ist standardmäßig nichts angehakt – nur ankreuzen, was wirklich fehlt (oder „Alle wählen“)
- **Verlauf & Statistik**: Änderungsverlauf (wer hat was wann geändert), meistgekochte Rezepte, Kochverlauf
- **Rückgängig** für jede Aktion (Button oben rechts), mehrfach hintereinander
- **Teilen**: Wer den Link kennt, kann Plan und Liste sehen und ändern. Kein Passwort, kein Benutzerkonto – beim ersten Öffnen wählt man nur seinen Namen (für den Verlauf).

## Starten mit Docker

```bash
cp .env.example .env      # Namen anpassen
docker compose up -d --build
docker compose exec mampfplan python -m app.cli link
```

Der letzte Befehl zeigt den **persönlichen Link** (`http://…/s/<geheimes-token>`). Den Link an die Familie schicken und als Lesezeichen / „Zum Startbildschirm hinzufügen“ speichern. Beim ersten Start steht er auch im Log (`docker compose logs mampfplan`).

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

### Offline im Supermarkt & Display an beim Kochen

- **Offline abhaken:** Die Einkaufsliste (und alle Seiten, die du schon einmal geöffnet hast) steht auch ohne Internet zur Verfügung. Abhaken und Umsortieren funktionieren offline weiter: Die Änderungen werden auf dem Handy gemerkt (Banner „📴 …“) und automatisch übertragen, sobald wieder Verbindung besteht. Wurde derselbe Artikel inzwischen von jemand anderem geändert, gilt der zuletzt gesendete Stand. Hinzufügen, Löschen und Rezeptsuche brauchen Verbindung.
- **Display bleibt an:** Solange eine Rezeptseite offen ist, schaltet das Handy das Display nicht aus.
- **Voraussetzung: HTTPS.** Beides nutzt Browser-Funktionen (Service Worker, Screen Wake Lock), die nur auf `https://…` oder `localhost` erlaubt sind – **nicht** über `http://192.168.x.x`. Betreibe die App deshalb hinter einem Reverse Proxy mit Zertifikat (Nginx Proxy Manager, Caddy, Traefik, Synology-Reverse-Proxy …) und ruf sie darüber auf.
- **Einmal vorher öffnen:** Die Liste muss auf dem Handy mindestens einmal online geladen worden sein (die App aktualisiert sie bei jedem Besuch im Hintergrund). Am besten die App zum Startbildschirm hinzufügen.

### Einstellungen (`.env`)

| Variable | Bedeutung |
| --- | --- |
| `MEMBERS` | Namen der Benutzer, kommagetrennt |
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
- **SSRF:** keine nutzergesteuerten URLs – nur fest hinterlegte API-Hosts, IDs werden per Regex validiert, Timeouts gesetzt. Der Bild-Cache lädt ausschließlich per `https` von fest hinterlegten Bildservern (Chefkoch, TheMealDB, Spoonacular), folgt keinen Weiterleitungen, begrenzt die Größe und prüft den Dateiinhalt (nur JPEG/PNG/GIF/WebP, nie SVG)
- **Bilder:** Die Content-Security-Policy erlaubt Bilder nur von der App selbst (`img-src 'self' data:`) – ein Bild von einem fremden Server würde der Browser gar nicht laden
- **Security-Header:** CSP, `X-Frame-Options`, `nosniff`, `Referrer-Policy: no-referrer`, `Cache-Control: no-store`, `X-Robots-Tag`
- **Hardening:** Container läuft als Nicht-Root, `read_only`, ohne Capabilities; keine API-Docs; Zugriffslog aus (Token steht im Pfad)
- **Eingaben:** serverseitige Längenlimits und Typprüfung

## Bekannte Grenzen

- Eigene Rezepte haben (noch) kein Bild: Ein frei eintragbares Bild von beliebigen Webseiten wäre nur mit direktem Abruf durch den Browser möglich. Ein Foto-Upload ließe sich nachrüsten.
- Die **Chefkoch-Schnittstelle ist inoffiziell** und kann sich jederzeit ändern oder wegfallen. Dann ist nur dieser Tab betroffen; eigene Rezepte und TheMealDB laufen weiter.
- Offline gemerkte Änderungen liegen nur auf dem jeweiligen Handy (Browser-Speicher). Löscht man die Website-Daten, bevor sie übertragen wurden, gehen sie verloren.
- „Rückgängig“ nimmt immer die **letzte** Änderung zurück – auch wenn sie vom anderen Familienmitglied stammt (der Button zeigt, was es ist).
- Es gibt nur eine minimale automatische Schema-Anpassung (`app/db.py: migrate`). Vor größeren Updates bitte die SQLite-Datei sichern.
- Zum Installieren auf dem iPhone-Startbildschirm wird ein SVG-Icon genutzt; ein PNG-Icon wäre noch schöner.
