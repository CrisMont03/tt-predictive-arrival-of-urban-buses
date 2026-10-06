"""Administrative CLI. Uses ADC/IAM, not client credentials. Never runs automatically."""
import argparse
from datetime import datetime, timezone


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["publish", "retire"])
    parser.add_argument("--id", required=True)
    parser.add_argument("--message")
    parser.add_argument("--severity", choices=["info", "warning", "critical"], default="info")
    parser.add_argument("--type", default="servicio")
    parser.add_argument("--expires-at", help="ISO timestamp with timezone")
    args = parser.parse_args()
    if "/" in args.id:
        parser.error("El identificador no debe contener /")
    now = datetime.now(timezone.utc)
    if args.action == "publish":
        if not args.message or not args.expires_at:
            parser.error("Publicar requiere mensaje y expiración")
        expiration = datetime.fromisoformat(args.expires_at)
        if expiration.tzinfo is None or expiration <= now:
            parser.error("La expiración debe ser futura y tener zona horaria")
    import firebase_admin
    from firebase_admin import firestore
    firebase_admin.initialize_app()
    ref = firestore.client().collection("alerts").document(args.id)
    if args.action == "retire":
        ref.update({"active": False})
    else:
        ref.set({"station_id": "rio_consulado", "message": args.message, "severity": args.severity, "type": args.type, "active": True, "created_at": now, "expires_at": expiration})
    print(f"{args.action}: {args.id}")


if __name__ == "__main__":
    main()
