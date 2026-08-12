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

Erreichbar unter <http://127.0.0.1:5001> (nur localhost gebunden, siehe
`compose.yaml`). Kein öffentlicher Port, keine Anbindung an Caddy/das
Automation Center in V1.

```bash
docker compose logs -f web
docker compose down
```

## Tests

```bash
docker compose run --rm tests
```

Läuft mit `network_mode: none` und ohne Zoho-Zugangsdaten - beweist, dass
die Testsuite offline läuft und nichts nach Zoho schreibt.

## Bedienung

1. Liefertag wählen, **Tour laden** - liest die an diesem Tag zu liefernden
   Zoho-Sales-Orders (Status `open`/`confirmed`/`invoiced`/`overdue`/
   `partially_invoiced`; `draft` sind Planungsentwürfe und zählen nicht als
   Lieferung), fasst mehrere Aufträge desselben Kunden zusammen und berechnet
   die Verpackungsmengen.
2. Stopps per Ziehen am `☰`-Griff neu sortieren (Maus und Touch), Kisten /
   Schnittware / Kresse direkt in der Zeile überschreiben - wirkt nur auf
   diesen Tourentwurf, nie auf Zoho oder die Artikelregeln.
3. **Profil speichern** merkt sich die aktuelle Kundenreihenfolge unter
   einem Namen (nur `customer_id`-Reihenfolge, keine Mengen). **Profil
   laden** wendet sie auf den aktuellen Tag an: Kunden ohne heutigen Auftrag
   werden ignoriert, neue Kunden ans Ende gesetzt und als "nicht im Profil"
   markiert.
4. **XLSX herunterladen** exportiert die aktuell sichtbare Reihenfolge mit
   den ggf. manuell überschriebenen Mengen.
5. Unklassifizierte Artikel erscheinen als deutliche Warnung und fließen
   nicht in die Summen ein.
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
7. sonst → unklassifiziert (Warnung, nicht in Summen)

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

- Ein Tourenstopp = ein GSMtasks-Order mit genau einem drop_off-Task
  (`POST /orders/`, `category: drop_off`).
- `external_id` je Stopp = `routeplanning::<Lieferdatum>::<Zoho-customer_id>`
  (deterministisch). GSMtasks erkennt daran bereits vorhandene Orders und
  dedupliziert serverseitig; zusätzlich blockiert die App selbst einen
  zweiten Versand, sobald ein Lieferdatum schon vollständig erfolgreich
  übertragen wurde.
- Reihenfolge wird über `position` (100, 200, 300, …) beim Anlegen gesetzt,
  in exakt der aktuellen Automation-Center-Reihenfolge.
- Keine Routenoptimierungs-API wird aufgerufen, kein `auto_assign`.
- Telefonnummer/E-Mail werden nur übernommen, wenn sie bereits in den
  ohnehin geladenen Zoho-Auftragsdaten vorhanden sind (keine Zusatzabfrage).

## Offene Punkte für spätere Iterationen

- Schreibzugriffe nach Zoho
- Integration ins zentrale Automation Center (Caddy, gemeinsames Netz)
- "Trotzdem erneut senden" nach vollständig erfolgreicher Übertragung
  (V1 blockiert das bewusst, um Duplikate zu vermeiden)
