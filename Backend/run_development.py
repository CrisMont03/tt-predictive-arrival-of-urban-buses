"""Firebase-backed development API; integration predictor requires an explicit flag."""
import argparse
import os


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--confirm-dev-project", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--predictor", choices=("unavailable", "integration"), default="unavailable")
    parser.add_argument("--context", choices=("fixture", "real"), default=None)
    args = parser.parse_args(argv)
    if args.project != args.confirm_dev_project or args.project.startswith("demo-"):
        parser.error("Confirma explícitamente el ID del proyecto real de desarrollo")
    if os.getenv("ARRIBO_MODEL_DIR"):
        parser.error("Este escenario requiere ARRIBO_MODEL_DIR sin configurar; no se cargará ningún modelo")
    if os.getenv("FIRESTORE_EMULATOR_HOST") or os.getenv("FIREBASE_AUTH_EMULATOR_HOST"):
        parser.error("No mezcles este escenario real de desarrollo con emuladores")
    if args.predictor == "integration" and args.project != "arribo-68326":
        parser.error("El predictor de prueba solo está autorizado para arribo-68326")
    if args.context and args.predictor != "integration":
        parser.error("--context requiere --predictor integration")
    if args.predictor == "integration" and os.getenv("K_SERVICE"):
        parser.error("El predictor de prueba no se permite en Cloud Run")
    return args


def main():
    args = arguments()
    import firebase_admin
    import uvicorn
    from app.main import create_app
    firebase_admin.initialize_app(options={"projectId": args.project})
    predictor = context_provider = None
    if args.predictor == "integration":
        from app.integration_predictor import IntegrationPredictor, integration_context
        predictor = IntegrationPredictor()
        predictor.metadata["context_profile"] = args.context or "fixture"
        context_provider = integration_context if args.context != "real" else None
        print("AVISO: predictor de integración SIMULADO; no es un LSTM. Datos excluidos del aprendizaje.")
    # Always real Firebase tokens, even when the explicitly chosen predictor is simulated.
    uvicorn.run(create_app(mode="production", predictor=predictor,
                           allow_integration=args.predictor == "integration",
                           context_provider=context_provider), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
