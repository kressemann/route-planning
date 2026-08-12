"""WSGI-Einstiegspunkt: ``gunicorn wsgi:app`` bzw. ``python wsgi.py`` fuer den
Entwicklungsserver."""

from werkzeug.middleware.proxy_fix import ProxyFix

from routeplanning.app import create_app

app = create_app()

# Hinter Caddy per Pfad (/tourenplanung) eingebunden: Caddy strippt den
# Praefix und setzt X-Forwarded-Prefix, damit url_for() weiterhin korrekte,
# praefixierte Links erzeugt. Ohne diesen Header (lokaler Dev-Server, Tests)
# bleibt SCRIPT_NAME unveraendert.
app.wsgi_app = ProxyFix(app.wsgi_app, x_prefix=1)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
