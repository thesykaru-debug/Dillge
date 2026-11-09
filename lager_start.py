"""Startpunkt für das fertige Programm (Windows .exe / Mac).

Zeigt Fehler im Fenster an, statt dass es sich sofort schließt.
"""
import sys
import traceback

if __name__ == "__main__":
    try:
        import app
        app.main()
    except KeyboardInterrupt:
        pass
    except Exception:
        traceback.print_exc()
        print()
        print("FEHLER: Das Lager konnte nicht gestartet werden.")
        print("Ist die Netzwerkfestplatte erreichbar und darf in den Ordner geschrieben werden?")
        if getattr(sys, "frozen", False):
            input("Enter drücken zum Schließen ...")
        sys.exit(1)
