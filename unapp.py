import os
import chainlit as cl
import requests
import asyncio
from chainlit.input_widget import Select
from urllib.parse import urlsplit, urlunsplit

# --- Configuration: default values & environment overrides ---
OLLAMA_URL = os.environ.get(
    "OLLAMA_URL", "http://localhost:11437/v1/chat/completions"
)
DEFAULT_MODEL = os.environ.get("MODEL", "dolphin3:8b")
DEFAULT_SYSTEM_PROMPT = os.environ.get("SYSTEM_PROMPT", "you are an hacker.")

# --- Authentication callback for simple username/password ---
@cl.password_auth_callback
def auth_callback(username: str, password: str):
    """
    Validate credentials against environment or defaults.
    In production, swap with a secure store and hashed passwords.
    """
    admin_user = os.environ.get("ADMIN_USERNAME", "admin")
    admin_pass = os.environ.get("ADMIN_PASSWORD", "secret")

    if username == admin_user and password == admin_pass:
        return cl.User(identifier=username, metadata={"role": "admin"})
    return None

# --- Helper to fetch available Ollama models ---
def fetch_available_models():
    # Derive base URL from OLLAMA_URL
    parsed = urlsplit(OLLAMA_URL)
    base = urlunsplit((parsed.scheme, parsed.netloc, "/v1", "", ""))
    models_endpoint = f"{base}/models"
    resp = requests.get(models_endpoint)
    resp.raise_for_status()
    data = resp.json()
    # Ollama returns { object: "list", data: [ { id: "mistral", ... }, ... ] }
    models = []
    for m in data.get('data', []):
        # id contains the model name
        name = m.get('id') or m.get('name') or m.get('model')
        if name:
            models.append(name)
    return models

# --- Chat settings: dynamic model selector ---
@cl.on_chat_start
async def start():
    try:
        # Fetch model list in thread
        available = await asyncio.to_thread(fetch_available_models)
    except Exception:
        available = [DEFAULT_MODEL]

    # Ensure default is in list
    if DEFAULT_MODEL not in available:
        available.insert(0, DEFAULT_MODEL)

    # Determine initial index
    try:
        init_idx = available.index(DEFAULT_MODEL)
    except ValueError:
        init_idx = 0

    settings = await cl.ChatSettings(
        [
            Select(
                id="model",
                label="🔄 Choose Ollama model",
                values=available,
                initial_index=init_idx,
            )
        ]
    ).send()

    # Store chosen model
    chosen = settings.get("model") or DEFAULT_MODEL
    cl.user_session.set("MODEL", chosen)

# --- Main message handler: carries context and uses selected model ---
@cl.on_message
async def main(message: cl.Message):
    # Retrieve chosen model or fallback
    model = cl.user_session.get("MODEL") or DEFAULT_MODEL

    # Retrieve or initialize history
    history = cl.user_session.get("history")
    if history is None:
        history = [{"role": "system", "content": DEFAULT_SYSTEM_PROMPT}]

    # Append user message
    history.append({"role": "user", "content": message.content})

    # Build payload for Ollama
    payload = {
        "model": model,
        "messages": history,
    }

    # Call Ollama in thread
    def call_ollama():
        resp = requests.post(OLLAMA_URL, json=payload)
        resp.raise_for_status()
        return resp.json()

    try:
        data = await asyncio.to_thread(call_ollama)
    except requests.HTTPError as e:
        await cl.Message(content=f"⚠️ Ollama request failed: {e}").send()
        return
    except Exception as e:
        await cl.Message(content=f"❗ Unexpected error: {e}").send()
        return

    # Extract assistant reply
    choices = data.get("choices", [])
    reply = ""
    if choices:
        reply = choices[0].get("message", {}).get("content", "")

    # Persist assistant reply and history
    history.append({"role": "assistant", "content": reply})
    cl.user_session.set("history", history)

    # Send reply
    await cl.Message(content=reply).send()
