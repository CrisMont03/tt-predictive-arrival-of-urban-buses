"""Read-only diagnostics of real sources. No model, Firebase, or simulated data."""
import asyncio
import json

from app.context import fetch_context


def main():
    try:
        result = asyncio.run(fetch_context())
    except Exception:
        print("No se pudo completar el diagnóstico. Revisa configuración y conectividad; no se imprimen credenciales.")
        return 1
    sources = result.pop("_sources")
    print(json.dumps({"context": result, "sources": sources,
                      "note": "null significa no verificable; este comando no crea predicciones ni escribe Firestore."},
                     ensure_ascii=False, indent=2))
    return 0 if all(value["status"] == "available" for value in sources.values()) else 2


if __name__ == "__main__":
    raise SystemExit(main())
