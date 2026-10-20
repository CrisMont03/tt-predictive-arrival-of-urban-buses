"""Run the existing Firebase-backed API without loading or replacing a model."""
import argparse
import os


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--confirm-dev-project", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    if args.project != args.confirm_dev_project or args.project.startswith("demo-"):
        parser.error("Confirma explícitamente el ID del proyecto real de desarrollo")
    if os.getenv("ARRIBO_MODEL_DIR"):
        parser.error("Este escenario requiere ARRIBO_MODEL_DIR sin configurar; no se cargará ningún modelo")
    if os.getenv("FIRESTORE_EMULATOR_HOST") or os.getenv("FIREBASE_AUTH_EMULATOR_HOST"):
        parser.error("No mezcles este escenario real de desarrollo con emuladores")
    import firebase_admin
    import uvicorn
    from app.main import create_app
    firebase_admin.initialize_app(options={"projectId": args.project})
    # 'production' selects existing Firebase Auth/Firestore adapters, not a deployed model.
    # No fake predictor, no demo token bypass, and /predict remains MODEL_NOT_READY.
    uvicorn.run(create_app(mode="production"), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
