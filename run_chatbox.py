"""Run Cite Counsel locally: .venv/Scripts/python.exe run_chatbox.py."""

if __name__ == "__main__":
    import uvicorn
    from core.chatbox_settings import configure
    settings = configure()
    from harness.app import create_app
    from harness.core import Harness
    app = create_app(Harness(model=settings["llm_model"]), max_upload_mb=settings["max_upload_mb"])

    print(f"Cite Counsel: http://127.0.0.1:{settings['port']}")
    print("Models, API keys and plugins are managed in the page's settings bar.")
    uvicorn.run(app, host="127.0.0.1", port=settings["port"], access_log=False)
