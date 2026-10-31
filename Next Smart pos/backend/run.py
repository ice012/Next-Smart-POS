import os

from app import create_app

app = create_app()

if __name__ == "__main__":
    app.run(
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "8000")),
        debug=bool(app.config.get("INTERACTIVE_DEBUGGER", False)),
        use_reloader=os.environ.get("RELOAD", "0") == "1",
    )
