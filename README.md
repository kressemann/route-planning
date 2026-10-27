# Tourenplanung (V1)

Liest Zoho-Inventory-Sales-Orders eines Liefertages, baut daraus Lieferstopps
mit Verpackungsmengen (Kisten / Schnittware / Kresse) und macht sie in einer
Weboberfläche manuell sortier- und editierbar.

**V1-Umfang:** Zoho-Zugriff bleibt vollständig lesend, keine Schreiboperationen
nach Zoho. Der fertig geplante Tourentwurf kann nach expliziter Bestätigung
im Web manuell als GSMtasks-Aufträge (drop_off-Tasks) übertragen werden -
nie automatisch, keine GSMtasks-Routenoptimierung. Persistiert werden
Tourentwürfe, Routenprofile und GSMtasks-Übertragungsprotokolle lokal in
SQLite.

## Einmalige Einrichtung

Zoho-Zugangsdaten (nur Lesezugriff genügt):

1. <https://api-console.zoho.eu/> → **Self Client** → **Generate Code**
2. Scope: `ZohoInventory.READ`
3. Client ID, Client Secret und den erzeugten Code gegen einen Refresh Token
   tauschen (analog `production-planning/setup_auth.py`)
4. Werte in `.env` eintragen (Vorlage: `.env.example`). Die Datei gehört
   nicht ins Git.

```bash
cp .env.example .env
mkdir -p data && chown "$(id -u):$(id -g)" data
```

## Starten

```bash
docker compose up -d web
```

Produktiv seit 15.10.2026, eingebunden ins zentrale Automation Center:
erreichbar über Caddy unter <https://automation.kressemann.de/tourenplanung/>
(Basic Auth des Automation Centers, Route in
`/opt/automation/infrastructure/caddy/Caddyfile`). Der Container hat keinen
Host-Port, sondern ist nur im internen Docker-Netz `automation-network`
erreichbar (siehe `compose.yaml`).

```bash
docker compose logs -f web
docker compose down
```

## Tests

```bash
docker compose run --rm tests      # Python (pytest)
docker compose run --rm tests-js   # Sortierlogik der Oberflaeche (node:test)
```

Läuft mit `network_mode: none` und ohne Zoho-Zugangsdaten - beweist, dass
die Testsuite offline läuft und nichts nach Zoho schreibt.

## Bedienung

1. Liefertag wählen, **Tour laden** - liest die an diesem Tag zu liefernden
   Zoho-Sales-Orders anhand ihres `shipment_date` (alle Status außer `draft` -
   Planungsentwürfe - und `void` - storniert; auch `shipped`/`fulfilled`
   zählen, weil Zoho sie nach dem Versandbuchen setzt), fasst mehrere Aufträge desselben Kunden zusammen und berechnet
   die Verpackungsmengen.
2. Stopps per Ziehen am `☰`-Griff neu sortieren (Maus und Touch) - in einer
   Bewegung beliebig weit, am Fensterrand scrollt die Liste automatisch mit.
   Alternativ die Stoppnummer direkt eingeben (Enter oder Feld verlassen):
   der Stopp trägt danach genau diese Nummer, alle anderen behalten ihre
   relative Reihenfolge (Eingaben außerhalb 1..N werden begrenzt, ungültige
   abgewiesen). Kisten / Schnittware / Kresse direkt in der Zeile
   überschreiben - wirkt nur auf diesen Tourentwurf, nie auf Zoho oder die
   Artikelregeln. Gespeichert wird nur eine vollständige Stoppliste (jeder
   Stopp genau einmal).
3. **Profil speichern** merkt sich die aktuelle Kundenreihenfolge unter
   einem Namen (nur `customer_id`-Reihenfolge, keine Mengen). **Profil
   laden** wendet sie auf den aktuellen Tag an: Kunden ohne heutigen Auftrag
   werden ignoriert, neue Kunden ans Ende gesetzt und als "nicht im Profil"
   markiert. Wird ein bestehendes Profil an einem Tag erneut gespeichert, an
   dem einzelne Profilkunden fehlen, bleiben diese im Profil (hinter ihrem
   bisherigen Vorgänger). Ein normales "Tour laden" verändert Profile nie.
4. **XLSX herunterladen** exportiert die aktuell sichtbare Reihenfolge mit
   den ggf. manuell überschriebenen Mengen.
5. Unklassifizierte Artikel, nicht ganzzahlige VE-Mengen und VE-Größen,
   die dem Artikelstamm widersprechen, erscheinen als deutliche Warnung und
   fließen nicht in die Summen ein. Lieferadressen ohne Straße/PLZ/Ort werden
   ebenfalls als Warnung ausgewiesen (ein fehlendes Land meldet erst die
   GSMtasks-Vorabprüfung).
6. **An GSMtasks übertragen** öffnet einen Bestätigungsdialog (Lieferdatum,
   Anzahl Stopps, Gesamtmengen). Erst **Jetzt übertragen** legt pro
   sichtbarem Stopp genau einen drop_off-Task in GSMtasks an - Reihenfolge,
   manuelle Mengen und gelöschte Stopps kommen exakt wie im aktuellen
   Tourentwurf. Ein Tourentwurf, der bereits vollständig erfolgreich
   übertragen wurde, kann in V1 nicht erneut gesendet werden (Duplikatschutz).

## Artikelklassifizierung

Zentral in `routeplanning/article_rules.yaml` gepflegt, Priorität:

1. explizite Overrides (Artikelname)
2. Zoho-Custom-Field `Produktart` = `Schnittware` (am Artikel-Stammsatz)
3. `Gartenkresse` im Namen → Kresse (1 VE = 12 Stk.)
4. `1/9` im Namen → Einzelstück (1 Kiste = 9 Stk.)
5. Grammangabe im Namen → Schnittware
6. `Stk.` im Namen → Schnittware
7. `Mix` im Namen → vollständige VE (1 bestellte Einheit = 1 Kiste)
8. sonst → unklassifiziert (Warnung, nicht in Summen)

Mengen je Lieferstopp:

- **Kisten** = ⌈Summe aller 1/9-Schalen / 9⌉ (Sorten teilen sich Kisten,
  angebrochene Kiste zählt voll) + vollständige VE (nur ganzzahlig, sonst
  Warnung statt Rundung). Weicht `VE Stück` im Artikelstamm bei einem
  1/9-Artikel von 9 ab, wird die Position nicht gezählt, sondern gewarnt.
- **Schnittware** = Anzahl bestellter Verkaufseinheiten.
- **Gartenkresse** = ⌈Stück / VE-Größe⌉, VE-Größe aus `VE Stück` im
  Artikelstamm, ersatzweise `ve_groesse` in `article_rules.yaml` (12).

## Aufbau

```
routeplanning/
  zoho.py             rein lesender REST-Client (kein POST/PUT/DELETE)
  gsmtasks.py          Verbindungstest + manuelle Schreibintegration (drop_off-Tasks)
  article_rules.yaml  zentrale Klassifizierungsregeln
  article_rules.py    wendet die Regeln an
  quantities.py       gruppiert Aufträge nach Kunde, berechnet Mengen
  db.py               SQLite: Tourentwürfe, Routenprofile, GSMtasks-Übertragungen
  xlsx_export.py       Excel-Export
  app.py              Flask-Routen (JSON-API + Weboberfläche)
  templates/, static/ Weboberfläche (kein Build-Schritt, kein CDN)
tests/                 pytest, keine echten Zoho-/GSMtasks-Netzwerkzugriffe
wsgi.py                Einstiegspunkt für gunicorn
```

## GSMtasks-Übertragung

- Auth: `Authorization: Token <GSMTASKS_API_KEY>` (OpenAPI `tokenAuth`). HTTP 401
  `"Invalid token."` bedeutet: Schlüssel ungültig/widerrufen - neuen Schlüssel in
  GSMtasks erzeugen und in `.env` ersetzen, danach `docker compose up -d web`.
- Ein Tourenstopp = ein GSMtasks-Order mit genau einem drop_off-Task
  (`POST /orders/`). Gesendet wird nur: `account`, `external_id`,
  `contact.name` (Kundenname), `address` (einzeilige `raw_address` ohne
  Ansprechpartnerzeile + `street`, `postal_code`, `city`, `country`),
  `position` (100, 200, … in Tour-Reihenfolge), `category: drop_off`,
  `complete_after`/`complete_before` = Liefertag 00:00:00–23:59:59
  Europe/Berlin mit korrektem UTC-Offset (ohne diese Felder ordnet GSMtasks
  die Aufgabe dem Übertragungszeitpunkt zu), `auto_assign: false`, `assignee` = User-URL des festen Fahrers (`GSMTASKS_ASSIGNEE_USER_ID`/`GSMTASKS_ASSIGNEE_NAME` in `compose.yaml`). Vor jeder Übertragung wird lesend per `GET /account_roles/?user=<uuid>` geprüft, dass genau eine aktive Fahrer-Rolle mit diesem Anzeigenamen existiert; sonst wird nichts gesendet (kein Fallback auf „Nicht zugewiesen“).
  Keine Routenoptimierung.
- Vor dem Senden (rein lesend): API-Schlüssel prüfen (`GET /accounts/`), für
  jeden noch nicht übertragenen Stopp `GET /orders/?external_id=…`. Vorhandene
  Orders werden übersprungen, unvollständige Adressen blockieren die gesamte
  Übertragung. Schlägt die Prüfung fehl, wird nichts gesendet.
- `external_id` je Stopp = `routeplanning::<Lieferdatum>::<Zoho-customer_id>`.
- Ergebnis je Stopp: angelegt / bereits vorhanden / nicht angelegt (4xx) /
  unklar (Timeout, Abbruch, 5xx) / nicht gesendet. Nach einem unklaren
  Ergebnis werden die restlichen Stopps nicht mehr gesendet; der nächste
  Versuch klärt den Stopp über die `external_id`-Suche - kein Duplikat.
- Ein erneuter Versuch sendet nur die noch fehlenden Stopps. Ist jeder Stopp
  übertragen, ist ein weiterer Versand gesperrt (HTTP 409). Bestehende
  GSMtasks-Daten werden nie geändert oder gelöscht.
- Der Bestätigungsdialog zeigt die Vorabprüfung und verlangt die Bestätigung,
  dass die Tour nicht bereits manuell in GSMtasks angelegt wurde (manuell
  angelegte Aufgaben tragen keine `external_id` und sind nicht erkennbar).
- Entwürfe, die vor Einführung der strukturierten Adresse geladen wurden,
  einmal per „Tour laden“ aktualisieren (Reihenfolge/Mengen bleiben erhalten).
- Erste vollständige produktive Übertragung: Tour vom 26.10.2026, 11 Stopps,
  alle angelegt (Batch-Status `success`).

### Lieferadresse: Auftragskopie vs. Kundenstamm

Zoho speichert in jedem Sales Order eine eigene Kopie der Lieferadresse.
Wird die Adresse im Kundenstamm später ergänzt, bleibt die Kopie im Auftrag
unverändert. Am 26.10.2026 blockierte deshalb „Fuxbau Johannes Neufeld“
(NA-05899): Die Auftragskopie hatte kein Land (Ort „Neustadt-Hambach“), der
Kundenstamm war vollständig (Weinstraße 240, 67434 Neustadt an der
Weinstraße, Deutschland, z. Hd. Küche).

Seitdem gilt beim „Tour laden“ (`quantities.resolve_shipping_address`): Ist
die Auftragsadresse für GSMtasks unvollständig (Straße, PLZ, Ort, Land), wird
die Standard-Lieferadresse des Kunden aus Zoho gelesen (nur `GET
/contacts/{id}`) und vollständig übernommen, aber nur, wenn sie selbst
vollständig ist **und** Straße mit Hausnummer sowie PLZ mit dem Auftrag
übereinstimmen. Andernfalls bleibt die Auftragsadresse und damit die Sperre.
Vollständige Auftragsadressen werden nie ersetzt. „Zu Händen“ erscheint nur
in der Anzeige, nie in der GSMtasks-Adresse. Regressionstests:
`tests/test_quantities_regression.py`.

### Dauerhafte Transfersperre je Liefertag

Wurde eine Tour bereits manuell in GSMtasks angelegt, wird ihre Übertragung
gesperrt (Tabelle `gsmtasks_transfer_locks`, unabhängig vom Tourentwurf -
bleibt bei „Tour laden“, Neustart und Deployment erhalten; Vorabprüfung und
Transfer antworten mit HTTP 423, die Oberfläche zeigt einen Sperrhinweis).
Kein Web-Endpunkt kann eine Sperre aufheben.

```bash
docker exec route-planning python -m routeplanning.admin lock-gsmtasks 2026-10-15 "Grund"
docker exec route-planning python -m routeplanning.admin show-gsmtasks-locks
docker exec route-planning python -m routeplanning.admin unlock-gsmtasks 2026-10-15 --confirm
```

## Offene Punkte für spätere Iterationen

- Schreibzugriffe nach Zoho
- "Trotzdem erneut senden" nach vollständig erfolgreicher Übertragung
  (V1 blockiert das bewusst, um Duplikate zu vermeiden)
