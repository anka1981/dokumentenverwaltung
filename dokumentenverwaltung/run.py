"""Startet die Dokumentenverwaltung.

    python run.py                    # nur auf diesem Rechner: http://localhost:8080
    python run.py --host 0.0.0.0     # auch vom Handy im WLAN erreichbar
"""

import argparse
import os

from dms import create_app, gdrive


def main():
    p = argparse.ArgumentParser(description="Dokumentenverwaltung")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--data", default=os.environ.get("DMS_DATA", "data"), help="Ablage- und Datenbankverzeichnis")
    args = p.parse_args()

    app = create_app(args.data)
    data_dir = app.config["DATA_DIR"]
    gdrive.start_scheduler(data_dir / "dms.sqlite", data_dir)
    if args.host not in ("127.0.0.1", "localhost") and not os.environ.get("DMS_PASSWORD") \
            and not os.environ.get("DMS_TRUSTED_IPS"):
        print("Hinweis: Ohne DMS_PASSWORD kann jeder im Netzwerk auf die Dokumente zugreifen.")
    try:
        from waitress import serve
    except ImportError:
        app.run(host=args.host, port=args.port, threaded=True)
    else:
        print(f"Dokumentenverwaltung läuft auf http://{args.host}:{args.port}")
        serve(app, host=args.host, port=args.port, max_request_body_size=200 * 1024 * 1024)


if __name__ == "__main__":
    main()
