"""Run the local HTTP Chatbox: .venv/Scripts/python.exe run_chatbox.py."""

if __name__ == "__main__":
    import uvicorn
    from core.chatbox_settings import configure
    settings = configure()
    from api.chatbox_app import create_app
    app = create_app(settings)

    print(f"Cite Counsel Chatbox: http://127.0.0.1:{settings['port']}")
    print("Configure config/chatbox.local.json, then restart to apply changes.")
    uvicorn.run(app, host="127.0.0.1", port=settings["port"], access_log=False)
