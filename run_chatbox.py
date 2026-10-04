"""Run Cite Counsel locally: .venv/Scripts/python.exe run_chatbox.py."""

if __name__ == "__main__":
    import logging
    import os
    import uvicorn
    # Each harness step (user message, tool call, result, reply) prints here.
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("harness").setLevel(logging.INFO)
    from core.chatbox_settings import configure
    settings = configure()
    from harness.app import create_app
    from harness.core import Harness
    app = create_app(Harness(model=settings["llm_model"]), max_upload_mb=settings["max_upload_mb"])

    # Loopback by default; the Docker image sets CITECOUNSEL_HOST=0.0.0.0 and publishes the port on 127.0.0.1 only.
    host = os.environ.get("CITECOUNSEL_HOST", "127.0.0.1")
    print(f"Cite Counsel: http://127.0.0.1:{settings['port']}")
    print("Models, API keys and plugins are managed in the page's settings bar.")
    uvicorn.run(app, host=host, port=settings["port"], access_log=False)
