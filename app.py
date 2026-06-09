import os
import argparse
import logging
import sqlite3
import re
import io
import platform
import shutil
import socket
import glob
import uuid
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, send_file
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import ollama
from groq import Groq
import requests
import yt_dlp

from docx import Document
from reportlab.lib.pagesizes import letter
from reportlab.lib.utils import simpleSplit
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

def parse_startup_args():
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument(
        '--no-local-models',
        '--skip-local-models',
        '--disable-local-models',
        action='store_true',
        dest='no_local_models'
    )
    args, _ = parser.parse_known_args()
    return args

STARTUP_ARGS = parse_startup_args()

if STARTUP_ARGS.no_local_models:
    whisper = None
else:
    import whisper

def load_env_file(path='.env'):
    if not os.path.exists(path):
        return

    with open(path, 'r', encoding='utf-8') as env_file:
        for raw_line in env_file:
            line = raw_line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue

            if line.startswith('export '):
                line = line[len('export '):].strip()

            key, value = line.split('=', 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")

            if key and key not in os.environ:
                os.environ[key] = value

load_env_file()

app = Flask(__name__)
app.logger.setLevel(logging.INFO)
app.secret_key = os.getenv('FLASK_SECRET_KEY', 'super-tajny-klucz-do-sesji-praktyki')

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'temp_uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

DB_FILE = 'users.db'
LOCAL_MODELS_ENABLED = not STARTUP_ARGS.no_local_models
OPENAI_MODEL = os.getenv('OPENAI_MODEL', 'gpt-4o-mini').strip() or 'gpt-4o-mini'
OPENAI_WEB_SEARCH_MODEL = os.getenv('OPENAI_WEB_SEARCH_MODEL', OPENAI_MODEL).strip() or OPENAI_MODEL
OPENAI_TRANSCRIBE_MODEL = os.getenv('OPENAI_TRANSCRIBE_MODEL', 'gpt-4o-mini-transcribe').strip() or 'gpt-4o-mini-transcribe'

PROVIDERS = {
    "groq": {
        "label": "Groq",
        "env_key": "GROQ_API_KEY"
    },
    "openai": {
        "label": "OpenAI",
        "env_key": "OPENAI_API_KEY"
    }
}

MODEL_TYPES = {
    "transcription": "Transkrypcja",
    "notes": "Notatki AI",    
    "chat": "Czat z AI"    
}

MODEL_CATALOG = {
    "groq": {
        "transcription": [
            {"id": "whisper-large-v3", "label": "Whisper Large V3"},
            {"id": "whisper-large-v3-turbo", "label": "Whisper Large V3 Turbo"}
        ],
        "chat": [
            {"id": "llama-3.3-70b-versatile", "label": "Llama 3.3 70B Versatile"},
            {"id": "llama-3.1-8b-instant", "label": "Llama 3.1 8B Instant"},
            {"id": "openai/gpt-oss-120b", "label": "GPT OSS 120B"},
            {"id": "openai/gpt-oss-20b", "label": "GPT OSS 20B"},
            {"id": "qwen/qwen3-32b", "label": "Qwen3 32B"},
            {"id": "meta-llama/llama-4-scout-17b-16e-instruct", "label": "Llama 4 Scout 17B 16E"}
        ]
    },
    "openai": {
        "transcription": [
            {"id": "whisper-1", "label": "Whisper 1"},
            {"id": "gpt-4o-mini-transcribe", "label": "GPT-4o mini transcribe"},
            {"id": "gpt-4o-transcribe", "label": "GPT-4o transcribe"},
            {"id": "gpt-4o-transcribe-diarize", "label": "GPT-4o transcribe diarize"}
        ],
        "chat": [
            {"id": f"{OPENAI_WEB_SEARCH_MODEL}", "label": f"{OPENAI_WEB_SEARCH_MODEL} (z .env)"},
            {"id": f"gpt-5.5", "label": f"GPT-5.5 - drogi model"},
        ],
        "notes": [
            {"id": f"{OPENAI_MODEL}", "label": f"{OPENAI_MODEL} (z .env)"},
            {"id": "gpt-4o-mini", "label": "GPT-4o mini"},
            {"id": "gpt-4o", "label": "GPT-4o"},
            {"id": "gpt-4.1-mini", "label": "GPT-4.1 mini"},
            {"id": "gpt-4.1", "label": "GPT-4.1"}
        ]
    }
}

LOCAL_MODEL_REQUIREMENTS = [
    {
        "id": "whisper-tiny",
        "name": "Whisper Tiny",
        "category": "Transkrypcja lokalna",
        "engine": "openai-whisper",
        "summary": "Najszybszy lokalny model STT. Dobry do krótkich nagrań i słabszego sprzętu.",
        "resource_usage": {
            "uses_local_hardware": True,
            "summary": "Informacja pokazuje, jakie zasoby model potrafi wykorzystać w tej aplikacji. Nie jest to bieżący pomiar użycia.",
            "capabilities": [
                {"name": "CPU", "status": "Tak", "state": "yes", "detail": "Może wykonywać transkrypcję na procesorze."},
                {"name": "RAM", "status": "Tak", "state": "yes", "detail": "Wymagana do załadowania modelu i przetwarzania audio."},
                {"name": "Dysk", "status": "Tak", "state": "yes", "detail": "Używany na pliki modelu oraz pliki tymczasowe audio."},
                {"name": "GPU NVIDIA / CUDA", "status": "Może", "state": "optional", "detail": "Użyje GPU, jeśli PyTorch widzi CUDA i model zostanie załadowany na CUDA."},
                {"name": "Apple Metal / MPS", "status": "Nie w tej konfiguracji", "state": "no", "detail": "Ten kod nie wybiera urządzenia MPS dla Whisper."}
            ]
        },
        "libraries": ["openai-whisper", "torch", "numpy", "ffmpeg"],
        "drivers": [
            "CPU: brak dodatkowych sterowników poza FFmpeg.",
            "GPU NVIDIA: opcjonalnie sterownik NVIDIA oraz CUDA zgodne z używaną wersją PyTorch."
        ],
        "hardware": {
            "RAM": "ok. 1 GB wolnej pamięci",
            "VRAM": "opcjonalnie ok. 1 GB",
            "Dysk": "ok. 75 MB na model",
            "CPU": "dowolny nowoczesny CPU; działa wolniej bez GPU"
        },
        "notes": ["Najmniejsze zużycie zasobów.", "Niższa dokładność niż Base i Small."]
    },
    {
        "id": "whisper-base",
        "name": "Whisper Base",
        "category": "Transkrypcja lokalna",
        "engine": "openai-whisper",
        "summary": "Domyślny kompromis między szybkością i jakością lokalnej transkrypcji.",
        "resource_usage": {
            "uses_local_hardware": True,
            "summary": "Informacja pokazuje, jakie zasoby model potrafi wykorzystać w tej aplikacji. Nie jest to bieżący pomiar użycia.",
            "capabilities": [
                {"name": "CPU", "status": "Tak", "state": "yes", "detail": "Może wykonywać transkrypcję na procesorze."},
                {"name": "RAM", "status": "Tak", "state": "yes", "detail": "Wymagana do załadowania modelu i przetwarzania audio."},
                {"name": "Dysk", "status": "Tak", "state": "yes", "detail": "Używany na pliki modelu oraz pliki tymczasowe audio."},
                {"name": "GPU NVIDIA / CUDA", "status": "Może", "state": "optional", "detail": "Użyje GPU, jeśli PyTorch widzi CUDA i model zostanie załadowany na CUDA."},
                {"name": "Apple Metal / MPS", "status": "Nie w tej konfiguracji", "state": "no", "detail": "Ten kod nie wybiera urządzenia MPS dla Whisper."}
            ]
        },
        "libraries": ["openai-whisper", "torch", "numpy", "ffmpeg"],
        "drivers": [
            "CPU: brak dodatkowych sterowników poza FFmpeg.",
            "GPU NVIDIA: opcjonalnie sterownik NVIDIA oraz CUDA zgodne z używaną wersją PyTorch."
        ],
        "hardware": {
            "RAM": "ok. 1-2 GB wolnej pamięci",
            "VRAM": "opcjonalnie ok. 1 GB",
            "Dysk": "ok. 150 MB na model",
            "CPU": "zalecany wielordzeniowy CPU"
        },
        "notes": ["Najbezpieczniejszy wybór dla większości nagrań.", "W aplikacji jest ustawiony jako model zrównoważony."]
    },
    {
        "id": "whisper-small",
        "name": "Whisper Small",
        "category": "Transkrypcja lokalna",
        "engine": "openai-whisper",
        "summary": "Dokładniejszy lokalny model STT, ale wolniejszy i bardziej wymagający.",
        "resource_usage": {
            "uses_local_hardware": True,
            "summary": "Informacja pokazuje, jakie zasoby model potrafi wykorzystać w tej aplikacji. Nie jest to bieżący pomiar użycia.",
            "capabilities": [
                {"name": "CPU", "status": "Tak", "state": "yes", "detail": "Może wykonywać transkrypcję na procesorze."},
                {"name": "RAM", "status": "Tak", "state": "yes", "detail": "Wymagana do załadowania modelu i przetwarzania audio."},
                {"name": "Dysk", "status": "Tak", "state": "yes", "detail": "Używany na pliki modelu oraz pliki tymczasowe audio."},
                {"name": "GPU NVIDIA / CUDA", "status": "Może", "state": "optional", "detail": "Użyje GPU, jeśli PyTorch widzi CUDA i model zostanie załadowany na CUDA."},
                {"name": "Apple Metal / MPS", "status": "Nie w tej konfiguracji", "state": "no", "detail": "Ten kod nie wybiera urządzenia MPS dla Whisper."}
            ]
        },
        "libraries": ["openai-whisper", "torch", "numpy", "ffmpeg"],
        "drivers": [
            "CPU: brak dodatkowych sterowników poza FFmpeg.",
            "GPU NVIDIA: opcjonalnie sterownik NVIDIA oraz CUDA zgodne z używaną wersją PyTorch."
        ],
        "hardware": {
            "RAM": "ok. 2-4 GB wolnej pamięci",
            "VRAM": "opcjonalnie ok. 2 GB",
            "Dysk": "ok. 500 MB na model",
            "CPU": "zalecany szybszy wielordzeniowy CPU"
        },
        "notes": ["Lepsza jakość dla trudniejszego audio.", "Na CPU może działać zauważalnie wolniej."]
    },
    {
        "id": "ollama-llama3",
        "name": "Llama 3 przez Ollama",
        "category": "Lokalne notatki i czat AI",
        "engine": "ollama",
        "summary": "Lokalny model językowy używany do generowania notatek i odpowiedzi w czacie.",
        "resource_usage": {
            "uses_local_hardware": True,
            "summary": "Informacja pokazuje, jakie zasoby Ollama potrafi wykorzystać. Nie jest to bieżący pomiar użycia.",
            "capabilities": [
                {"name": "CPU", "status": "Tak", "state": "yes", "detail": "Może generować odpowiedzi na procesorze."},
                {"name": "RAM", "status": "Tak", "state": "yes", "detail": "Wymagana do utrzymania modelu w pamięci."},
                {"name": "Dysk", "status": "Tak", "state": "yes", "detail": "Używany na pobrany model Ollama."},
                {"name": "GPU NVIDIA / CUDA", "status": "Może", "state": "optional", "detail": "Może użyć, jeśli Ollama i sterownik NVIDIA wspierają akcelerację."},
                {"name": "Apple Metal", "status": "Może", "state": "optional", "detail": "Może użyć na wspieranym macOS i sprzęcie Apple."},
                {"name": "AMD / ROCm", "status": "Może", "state": "optional", "detail": "Zależy od systemu, sterownika i wariantu Ollama."}
            ]
        },
        "libraries": ["ollama", "model llama3 pobrany przez `ollama run llama3`"],
        "drivers": [
            "CPU: działa bez dodatkowych sterowników GPU.",
            "GPU NVIDIA: opcjonalnie aktualny sterownik NVIDIA obsługiwany przez Ollama.",
            "macOS: Ollama może korzystać z Metal na wspieranym sprzęcie Apple."
        ],
        "hardware": {
            "RAM": "minimum ok. 8 GB, zalecane 16 GB",
            "VRAM": "opcjonalnie 4-8 GB zależnie od wariantu modelu",
            "Dysk": "kilka GB na pobrany model",
            "CPU": "zalecany wielordzeniowy CPU"
        },
        "notes": ["Wymaga uruchomionej usługi Ollama.", "Jeśli Ollama nie działa, aplikacja zwróci komunikat o braku lokalnych notatek AI."]
    }
]

DEFAULT_AI_MODELS = [
    {
        "provider": "groq",
        "model_type": "transcription",
        "display_name": "Whisper Large V3",
        "model_id": "whisper-large-v3",
        "is_default": 1
    },
    {
        "provider": "groq",
        "model_type": "chat",
        "display_name": "Llama 3.3 70B Versatile",
        "model_id": "llama-3.3-70b-versatile",
        "is_default": 1
    },
    {
        "provider": "openai",
        "model_type": "transcription",
        "display_name": "Whisper-1 API",
        "model_id": "whisper-1",
        "is_default": 1
    },
    {
        "provider": "openai",
        "model_type": "transcription",
        "display_name": "Whisper GPT-4o-mini-transcribe",
        "model_id": "gpt-4o-mini-transcribe",
        "is_default": 1
    },
    {
        "provider": "openai",
        "model_type": "notes",
        "display_name": "GPT-4o mini",
        "model_id": "gpt-4o-mini",
        "is_default": 1
    },
    {
        "provider": "openai",
        "model_type": "chat",
        "display_name": "GPT-5.4",
        "model_id": "gpt-5.4",
        "is_default": 1
    }
]

def get_db_connection():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def get_provider_label(provider):
    return PROVIDERS.get(provider, {}).get("label", provider)

def get_provider_api_key(provider):
    env_key = PROVIDERS.get(provider, {}).get("env_key")
    if not env_key:
        return ""
    return os.getenv(env_key, "").strip()

def get_provider_env_key(provider):
    return PROVIDERS.get(provider, {}).get("env_key", "API_KEY")

def require_provider_api_key(provider):
    api_key = get_provider_api_key(provider)
    if not api_key:
        env_key = get_provider_env_key(provider)
        raise RuntimeError(f"Brak klucza {env_key} w pliku .env")
    return api_key

def raise_invalid_api_key_error(provider):
    env_key = get_provider_env_key(provider)
    provider_label = get_provider_label(provider)
    raise RuntimeError(f"Nieprawidłowy klucz {env_key} dla {provider_label}. Sprawdź wartość w pliku .env.")

def raise_provider_api_error(provider, error):
    message = str(error)
    normalized_message = message.lower()
    if "invalid_api_key" in normalized_message or "invalid api key" in normalized_message or "401" in normalized_message:
        raise_invalid_api_key_error(provider)

    provider_label = get_provider_label(provider)
    raise RuntimeError(f"Błąd API {provider_label}: {message}")

def model_row_to_dict(row):
    model = dict(row)
    model["provider_label"] = get_provider_label(model["provider"])
    model["type_label"] = MODEL_TYPES.get(model["model_type"], model["model_type"])
    model["has_api_key"] = bool(get_provider_api_key(model["provider"]))
    return model

def seed_default_ai_models(cursor):
    for model in DEFAULT_AI_MODELS:
        cursor.execute(
            "SELECT id FROM ai_models WHERE provider = ? AND model_type = ? AND model_id = ?",
            (model["provider"], model["model_type"], model["model_id"])
        )
        if cursor.fetchone():
            continue

        cursor.execute(
            """
            INSERT INTO ai_models (provider, model_type, display_name, model_id, enabled, is_default)
            VALUES (?, ?, ?, ?, 1, ?)
            """,
            (
                model["provider"],
                model["model_type"],
                model["display_name"],
                model["model_id"],
                model["is_default"]
            )
        )

    for provider in PROVIDERS:
        for model_type in MODEL_TYPES:
            cursor.execute(
                """
                SELECT id FROM ai_models
                WHERE provider = ? AND model_type = ? AND is_default = 1
                LIMIT 1
                """,
                (provider, model_type)
            )
            if cursor.fetchone():
                continue

            cursor.execute(
                """
                UPDATE ai_models
                SET is_default = 1
                WHERE id = (
                    SELECT id FROM ai_models
                    WHERE provider = ? AND model_type = ? AND enabled = 1
                    ORDER BY id ASC
                    LIMIT 1
                )
                """,
                (provider, model_type)
            )

def list_ai_models(model_type=None, enabled_only=False):
    conn = get_db_connection()
    cursor = conn.cursor()
    query = "SELECT * FROM ai_models"
    filters = []
    params = []

    if model_type:
        filters.append("model_type = ?")
        params.append(model_type)
    if enabled_only:
        filters.append("enabled = 1")

    if filters:
        query += " WHERE " + " AND ".join(filters)

    query += """
        ORDER BY
            model_type DESC,
            provider ASC,
            is_default DESC,
            display_name ASC
    """

    cursor.execute(query, params)
    rows = cursor.fetchall()
    conn.close()
    return [model_row_to_dict(row) for row in rows]

def get_ai_model_by_id(model_id, model_type=None, enabled_only=True):
    try:
        model_pk = int(model_id)
    except (TypeError, ValueError):
        return None

    conn = get_db_connection()
    cursor = conn.cursor()
    query = "SELECT * FROM ai_models WHERE id = ?"
    params = [model_pk]

    if model_type:
        query += " AND model_type = ?"
        params.append(model_type)
    if enabled_only:
        query += " AND enabled = 1"

    cursor.execute(query, params)
    row = cursor.fetchone()
    conn.close()
    return model_row_to_dict(row) if row else None

def get_default_ai_model(model_type, preferred_provider=None):
    conn = get_db_connection()
    cursor = conn.cursor()

    if preferred_provider:
        cursor.execute(
            """
            SELECT * FROM ai_models
            WHERE model_type = ? AND provider = ? AND enabled = 1
            ORDER BY is_default DESC, id ASC
            LIMIT 1
            """,
            (model_type, preferred_provider)
        )
        row = cursor.fetchone()
        if row:
            conn.close()
            return model_row_to_dict(row)

    cursor.execute(
        """
        SELECT * FROM ai_models
        WHERE model_type = ? AND enabled = 1
        ORDER BY is_default DESC, provider ASC, id ASC
        LIMIT 1
        """,
        (model_type,)
    )
    row = cursor.fetchone()
    conn.close()
    return model_row_to_dict(row) if row else None

def get_selected_transcription_model(cloud_model_id):
    selected_model = get_ai_model_by_id(cloud_model_id, "transcription")
    if selected_model:
        return selected_model
    return get_default_ai_model("transcription")

def list_available_openai_chat_models():
    return [
        model
        for model in list_ai_models(model_type="chat", enabled_only=True)
        if model["provider"] == "openai" and model["has_api_key"]
    ]

def get_default_openai_chat_model():
    openai_chat_models = list_available_openai_chat_models()
    for model in openai_chat_models:
        if model["is_default"]:
            return model
    return openai_chat_models[0] if openai_chat_models else None

def get_selected_openai_chat_model(chat_model_id=None):
    if chat_model_id:
        selected_model = get_ai_model_by_id(chat_model_id, "chat")
        if not selected_model or selected_model["provider"] != "openai" or not selected_model["has_api_key"]:
            raise ValueError("Wybrany model czatu OpenAI nie jest dostępny.")
        return selected_model

    selected_model = get_default_openai_chat_model()
    if selected_model:
        return selected_model

    raise ValueError("Brak dostępnego modelu OpenAI typu chat. Sprawdź ustawienia modeli i klucz OPENAI_API_KEY.")

def promote_first_enabled_model_as_default(cursor, provider, model_type):
    cursor.execute(
        """
        SELECT id FROM ai_models
        WHERE provider = ? AND model_type = ? AND enabled = 1
        ORDER BY id ASC
        LIMIT 1
        """,
        (provider, model_type)
    )
    replacement = cursor.fetchone()
    if not replacement:
        return

    cursor.execute(
        "UPDATE ai_models SET is_default = 1 WHERE id = ?",
        (replacement[0],)
    )

def raise_for_openai_error(response):
    if response.ok:
        return

    try:
        error_payload = response.json()
        message = error_payload.get("error", {}).get("message") or response.text
        code = error_payload.get("error", {}).get("code", "")
    except ValueError:
        message = response.text
        code = ""

    if response.status_code == 401 or code == "invalid_api_key":
        raise_invalid_api_key_error("openai")

    raise RuntimeError(f"Błąd OpenAI API ({response.status_code}): {message}")

def transcribe_with_cloud(model_config, file_path, language):
    provider = model_config["provider"]
    api_key = require_provider_api_key(provider)

    if provider == "groq":
        client = Groq(api_key=api_key)
        try:
            with open(file_path, "rb") as audio_file:
                transcription_options = {
                    "file": (audio_file.name, audio_file.read()),
                    "model": model_config["model_id"]
                }
                if language != "auto":
                    transcription_options["language"] = language

                transcription = client.audio.transcriptions.create(**transcription_options)
                return transcription.text
        except Exception as error:
            raise_provider_api_error(provider, error)

    if provider == "openai":
        with open(file_path, "rb") as audio_file:
            files = {
                "file": (os.path.basename(file_path), audio_file)
            }
            data = {
                "model": model_config["model_id"]
            }
            if language != "auto":
                data["language"] = language

            response = requests.post(
                "https://api.openai.com/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {api_key}"},
                data=data,
                files=files,
                timeout=180
            )
            raise_for_openai_error(response)
            return response.json().get("text", "")

    raise RuntimeError(f"Nieobsługiwany provider: {provider}")

def chat_with_cloud(messages, model_config):
    if not model_config:
        raise RuntimeError("Brak aktywnego modelu AI w ustawieniach")

    provider = model_config["provider"]
    api_key = require_provider_api_key(provider)

    if provider == "groq":
        client = Groq(api_key=api_key)
        try:
            completion = client.chat.completions.create(
                model=model_config["model_id"],
                messages=messages
            )
            return completion.choices[0].message.content
        except Exception as error:
            raise_provider_api_error(provider, error)

    if provider == "openai" and model_config.get("model_type") == "notes":
        return chat_with_openai_responses(messages, model_config)

    if provider == "openai":
        response = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            },
            json={
                "model": model_config["model_id"],
                "messages": messages
            },
            timeout=180
        )
        raise_for_openai_error(response)
        payload = response.json()
        return payload["choices"][0]["message"]["content"]

    raise RuntimeError(f"Nieobsługiwany provider: {provider}")

def extract_responses_output_text(payload):
    if payload.get("output_text"):
        return payload["output_text"]

    chunks = []
    for item in payload.get("output", []):
        if item.get("type") != "message":
            continue
        for content in item.get("content", []):
            text = content.get("text")
            if text:
                chunks.append(text)

    return "\n".join(chunks).strip()

def extract_responses_sources(payload):
    sources = []
    seen_urls = set()

    def add_source(url, title=None):
        if not url or url in seen_urls:
            return
        seen_urls.add(url)
        sources.append({
            "url": url,
            "title": title or url
        })

    for item in payload.get("output", []):
        if item.get("type") == "web_search_call":
            action = item.get("action") or {}
            for source in action.get("sources") or []:
                add_source(source.get("url"), source.get("title"))

        if item.get("type") == "message":
            for content in item.get("content", []):
                for annotation in content.get("annotations", []) or []:
                    if annotation.get("type") == "url_citation":
                        add_source(annotation.get("url"), annotation.get("title"))

    return sources

def messages_to_responses_input(messages):
    role_labels = {
        "system": "Instrukcje systemowe",
        "user": "Użytkownik",
        "assistant": "Asystent"
    }
    prompt_parts = []
    for message in messages:
        role = role_labels.get(message.get("role"), message.get("role", "Wiadomość"))
        prompt_parts.append(f"{role}:\n{message.get('content', '')}")

    return "\n\n".join(prompt_parts)

def create_openai_responses(model_id, input_text, tools=None, include=None, timeout=180):
    api_key = require_provider_api_key("openai")
    payload = {
        "model": model_id,
        "input": input_text
    }
    if tools:
        payload["tools"] = tools
    if include:
        payload["include"] = include
    if tools:
        payload["tool_choice"] = "auto"

    response = requests.post(
        "https://api.openai.com/v1/responses",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        },
        json=payload,
        timeout=timeout
    )
    raise_for_openai_error(response)
    return response.json()

def chat_with_openai_responses(messages, model_config):
    payload = create_openai_responses(
        model_config["model_id"],
        messages_to_responses_input(messages),
        timeout=180
    )
    output_text = extract_responses_output_text(payload)
    if not output_text:
        raise RuntimeError("OpenAI Responses API nie zwróciło tekstu odpowiedzi.")

    return output_text

def build_web_search_question_prompt(transcription, context_rows, question):
    previous_messages = []
    for role, content in context_rows:
        speaker = "Użytkownik" if role == "user" else "Asystent"
        previous_messages.append(f"{speaker}: {content}")

    history_text = "\n".join(previous_messages) if previous_messages else "Brak wcześniejszej rozmowy."

    return (
        "Jesteś asystentem analizującym transkrypcję nagrania. Odpowiadaj po polsku.\n"
        "Masz dostęp do narzędzia web_search. Używaj go wtedy, gdy pytanie wymaga aktualnych, "
        "zewnętrznych lub weryfikowalnych informacji spoza transkrypcji. Jeśli odpowiedź wynika "
        "wyłącznie z transkrypcji, oprzyj się na transkrypcji i jasno to zaznacz.\n"
        "Nie zmyślaj faktów. Gdy korzystasz z internetu, podaj krótką odpowiedź i wskaż źródła.\n\n"
        "Kontekst poprzedniej rozmowy:\n"
        f"{history_text}\n\n"
        "Transkrypcja nagrania:\n"
        f"{transcription}\n\n"
        "Pytanie użytkownika:\n"
        f"{question}"
    )

def analyze_with_web_search(yt_response_prompt, model_config=None):
    model_id = model_config["model_id"] if model_config else OPENAI_WEB_SEARCH_MODEL
    payload = create_openai_responses(
        model_id,
        yt_response_prompt,
        tools=[
            {
                "type": "web_search"
            }
        ],
        include=["web_search_call.action.sources"],
        timeout=240
    )

    output_text = extract_responses_output_text(payload)
    if not output_text:
        raise RuntimeError("OpenAI Responses API nie zwróciło tekstu odpowiedzi.")

    return {
        "text": output_text,
        "sources": extract_responses_sources(payload),
        "model": model_id,
        "engine": f"{describe_cloud_model(model_config)} + web_search" if model_config else f"OpenAI Responses API ({model_id}) + web_search"
    }

def answer_question_with_ai(yt_response_prompt, model_config):
    if not model_config:
        raise RuntimeError("Brak aktywnego modelu czatu w ustawieniach")

    if model_config["provider"] == "openai":
        return analyze_with_web_search(yt_response_prompt, model_config)

    answer_text = chat_with_cloud([{"role": "user", "content": yt_response_prompt}], model_config)
    return {
        "text": answer_text,
        "sources": [],
        "model": model_config["model_id"],
        "engine": describe_chat_answer_model(model_config)
    }

def describe_cloud_model(model_config):
    if not model_config:
        return "Brak modelu"
    return f"{model_config['provider_label']} ({model_config['display_name']})"

def describe_local_notes_model():
    return "Ollama (Llama 3)"

def describe_notes_model(model_config=None, processing_mode=None):
    if model_config:
        return describe_cloud_model(model_config)
    if processing_mode == "online":
        return "Brak modelu notatek"
    return describe_local_notes_model()

def describe_chat_answer_model(model_config):
    label = describe_cloud_model(model_config)
    if model_config and model_config.get("provider") == "openai":
        return f"{label} + web_search"
    return label

def is_port_available(host, port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError:
            return False
        return True

def find_available_port(start_port, host='0.0.0.0', max_attempts=50):
    for port in range(start_port, start_port + max_attempts):
        if is_port_available(host, port):
            return port

    last_port = start_port + max_attempts - 1
    raise RuntimeError(f"Brak wolnego portu w zakresie {start_port}-{last_port}")

def get_start_port(default_port=8000):
    raw_port = os.getenv('PORT', str(default_port)).strip()
    try:
        return int(raw_port)
    except ValueError:
        print(f"Niepoprawna wartość PORT={raw_port}. Używam portu {default_port}.")
        return default_port

def get_server_port(host='0.0.0.0', default_port=8000):
    selected_port = os.getenv('APP_SELECTED_PORT')
    if selected_port:
        try:
            return int(selected_port)
        except ValueError:
            os.environ.pop('APP_SELECTED_PORT', None)

    start_port = get_start_port(default_port)
    port = find_available_port(start_port, host)
    os.environ['APP_SELECTED_PORT'] = str(port)

    if port != start_port:
        print(f"Port {start_port} jest zajęty. Uruchamiam aplikację na porcie {port}.")

    return port

def find_deno_runtime():
    candidate_paths = [
        os.getenv('YT_DLP_DENO_PATH', '').strip(),
        shutil.which('deno'),
        os.path.expanduser('~/.deno/bin/deno')
    ]

    for path in candidate_paths:
        if path and os.path.isfile(path) and os.access(path, os.X_OK):
            return path

    return None

def build_youtube_download_options(download_token):
    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': os.path.join(UPLOAD_FOLDER, f'{download_token}_%(id)s.%(ext)s'),
        'quiet': True,
        'noplaylist': True,
        'overwrites': True
    }

    deno_path = find_deno_runtime()
    if deno_path:
        ydl_opts['js_runtimes'] = {'deno': {'path': deno_path}}

    return ydl_opts

def make_temp_upload_path(filename):
    safe_name = secure_filename(filename) or "upload"
    return os.path.join(UPLOAD_FOLDER, f"{uuid.uuid4().hex}_{safe_name}")

def resolve_youtube_download_path(ydl, info, download_token):
    prepared_path = ydl.prepare_filename(info)
    candidates = [prepared_path]

    for download in info.get("requested_downloads") or []:
        filepath = download.get("filepath")
        if filepath:
            candidates.append(filepath)

    video_id = info.get("id")
    if video_id:
        candidates.extend(glob.glob(os.path.join(UPLOAD_FOLDER, f"{download_token}_{video_id}.*")))

    for candidate in candidates:
        if candidate and os.path.exists(candidate):
            return candidate

    raise FileNotFoundError(f"Nie znaleziono pobranego pliku YouTube dla tokenu {download_token}")

def download_youtube_audio(youtube_url):
    download_token = uuid.uuid4().hex
    ydl_opts = build_youtube_download_options(download_token)

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(youtube_url, download=True)
        return {
            "file_path": resolve_youtube_download_path(ydl, info, download_token),
            "title": info.get("title", "YouTube Video"),
            "id": info.get("id"),
            "duration": info.get("duration"),
            "webpage_url": info.get("webpage_url") or youtube_url
        }

def build_audio_notes_prompt(raw_text):
    verification_json_format = (
        '{\n'
        '  "prompt": "Napisz dokłądny prompt dla modelu i opisz w nim dokładnie, jakie frazy i źródła należy sprawdzić, aby potwierdzić autentyczność tekstu.",\n'
        '  "transcription": "Wstaw tutaj pełną transkrypcję do weryfikacji"\n'
        '}'
    )

    return (
        "Jesteś profesjonalnym asystentem biurowym. Przeczytaj uważnie poniższy tekst "
        "pochodzący z nagrania audio i przygotuj z niego czytelną, ustrukturyzowaną "
        "notatkę w języku polskim.\n"
        "Notatka MUSI składać się z trzech wyraźnych sekcji:\n"
        "1. KRÓTKIE PODSUMOWANIE (2-4 zdania wyjaśniające esencję nagrania).\n"
        "2. NAJWAŻNIEJSZE PUNKTY (kluczowe informacje i wątki wypisane od myślników).\n"
        "3. LISTA ZADAŃ DO WYKONANIA (określenie zadań i akcji do podjęcia, jeśli o nich wspomniano).\n"
        "4. Jeśli treść zawiera twierdzenia faktograficzne wymagające weryfikacji, dodaj sekcję "
        "'PROMPT DO SPRAWDZENIA AUTENTYCZNOŚCI'. W tej sekcji opisz, jak przeszukać internet "
        "i jakie źródła porównać, aby potwierdzić, czy tekst jest autentyczny i nie jest fake newsem.\n"
        "   Sekcję 'PROMPT DO SPRAWDZENIA AUTENTYCZNOŚCI' przedstaw jako poprawny JSON w formacie:\n"
        f"{verification_json_format}\n\n"
        f"Oto tekst do przeanalizowania:\n{raw_text}"
    )

def generate_audio_notes(raw_text, processing_mode, preferred_provider=None):
    if not raw_text.strip():
        return "", None

    prompt = build_audio_notes_prompt(raw_text)

    if processing_mode == "online":
        notes_model = get_default_ai_model("notes", preferred_provider=preferred_provider)
        return chat_with_cloud([{"role": "user", "content": prompt}], notes_model), notes_model

    try:
        response = ollama.chat(model='llama3', messages=[{'role': 'user', 'content': prompt}])
        return response['message']['content'], None
    except Exception:
        return "Nie udało się wygenerować notatek AI. Upewnij się, że Ollama działa w tle.", None

def normalize_processing_mode(processing_mode):
    mode = str(processing_mode or "offline").strip().lower()
    if mode not in {"offline", "online"}:
        raise ValueError("Nieobsługiwany tryb przetwarzania. Użyj 'offline' albo 'online'.")
    return mode

def get_default_processing_mode():
    return "offline" if LOCAL_MODELS_ENABLED else "online"

def process_audio_transcription(file_path, processing_mode='offline', model_name='base', cloud_model_id=None, language='auto', task='transcribe'):
    processing_mode = normalize_processing_mode(processing_mode)
    model_name = str(model_name or "base").strip()
    language = str(language or "auto").strip()
    task = str(task or "transcribe").strip()

    if task not in {"transcribe", "translate"}:
        raise ValueError("Nieobsługiwane zadanie. Użyj 'transcribe' albo 'translate'.")

    if processing_mode == "online":
        transcription_model = get_selected_transcription_model(cloud_model_id)
        if not transcription_model:
            raise ValueError("Brak aktywnego modelu transkrypcji w ustawieniach")

        raw_text = transcribe_with_cloud(transcription_model, file_path, language)
        notes, notes_model = generate_audio_notes(
            raw_text,
            processing_mode,
            preferred_provider=transcription_model["provider"]
        )

        return {
            "text": raw_text,
            "notes": notes,
            "notes_model_used": describe_notes_model(notes_model, processing_mode),
            "language": language if language != "auto" else "auto",
            "task": task,
            "model_used": f"{describe_cloud_model(transcription_model)} + notatki: {describe_cloud_model(notes_model)}"
        }

    if not LOCAL_MODELS_ENABLED:
        raise ValueError("Lokalne modele Whisper są wyłączone. Użyj trybu chmurowego albo uruchom aplikację bez parametru --no-local-models.")

    if model_name not in models:
        raise ValueError("Model lokalny nie jest obsługiwany")

    model = models[model_name]
    options = {"task": task, "fp16": False}
    if language != "auto":
        options["language"] = language

    result = model.transcribe(file_path, **options)
    raw_text = result["text"]
    notes, _ = generate_audio_notes(raw_text, processing_mode)

    return {
        "text": raw_text,
        "notes": notes,
        "notes_model_used": describe_notes_model(None, processing_mode),
        "language": result.get("language", language),
        "task": task,
        "model_used": f"Lokalny Whisper ({model_name})"
    }

def save_transcription_history(user_email, display_title, raw_text, notes, notes_model_used=""):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO history (user_email, filename, raw_text, ai_notes, notes_model_used) VALUES (?, ?, ?, ?, ?)",
        (user_email, display_title, raw_text.strip(), notes.strip(), str(notes_model_used or "").strip())
    )
    record_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return record_id

def get_payload_setting(payload, settings, key, default=None):
    if key in settings:
        return settings.get(key)
    return payload.get(key, default)

def parse_bool_setting(value, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "tak", "on"}
    return bool(value)

def build_local_model_list(model_type=None):
    if not LOCAL_MODELS_ENABLED:
        return []

    requirements = {item["id"]: item for item in LOCAL_MODEL_REQUIREMENTS}
    model_order = {"tiny": 1, "base": 2, "small": 3}
    local_models = []

    if model_type in (None, "transcription"):
        for model_name in sorted(models.keys(), key=lambda name: model_order.get(name, 99)):
            requirement = requirements.get(f"whisper-{model_name}", {})
            local_models.append({
                "source": "local",
                "id": model_name,
                "model_id": model_name,
                "model_type": "transcription",
                "type_label": MODEL_TYPES["transcription"],
                "display_name": requirement.get("name", f"Whisper {model_name.title()}"),
                "engine": requirement.get("engine", "openai-whisper"),
                "enabled": True,
                "available": True,
                "is_default": model_name == "base",
                "summary": requirement.get("summary", ""),
                "request_settings": {
                    "processing_mode": "offline",
                    "model_name": model_name
                }
            })

    for local_chat_type in ("chat", "notes"):
        if model_type not in (None, local_chat_type):
            continue

        requirement = requirements.get("ollama-llama3", {})
        local_models.append({
            "source": "local",
            "id": "ollama-llama3" if local_chat_type == "chat" else "ollama-llama3-notes",
            "model_id": "llama3",
            "model_type": local_chat_type,
            "type_label": MODEL_TYPES[local_chat_type],
            "display_name": requirement.get("name", "Llama 3 przez Ollama"),
            "engine": requirement.get("engine", "ollama"),
            "enabled": True,
            "available": True,
            "is_default": True,
            "summary": requirement.get("summary", ""),
            "request_settings": {
                "processing_mode": "offline",
                "chat_model": "llama3"
            }
        })

    return local_models

def build_cloud_model_list(model_type=None, include_disabled=False):
    cloud_models = []
    for model in list_ai_models(model_type=model_type, enabled_only=not include_disabled):
        enabled = bool(model["enabled"])
        has_api_key = bool(model["has_api_key"])
        cloud_models.append({
            "source": "cloud",
            "id": model["id"],
            "provider": model["provider"],
            "provider_label": model["provider_label"],
            "model_id": model["model_id"],
            "model_type": model["model_type"],
            "type_label": model["type_label"],
            "display_name": model["display_name"],
            "enabled": enabled,
            "available": enabled and has_api_key,
            "is_default": bool(model["is_default"]),
            "request_settings": {
                "processing_mode": "online",
                "cloud_model_id": model["id"]
            }
        })

    return cloud_models

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            email TEXT PRIMARY KEY,
            first_name TEXT NOT NULL,
            last_name TEXT NOT NULL,
            password_hash TEXT NOT NULL
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_email TEXT NOT NULL,
            filename TEXT NOT NULL,
            raw_text TEXT NOT NULL,
            ai_notes TEXT NOT NULL,
            notes_model_used TEXT DEFAULT '',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_email) REFERENCES users(email)
        )
    ''')
    cursor.execute("PRAGMA table_info(history)")
    history_columns = {row[1] for row in cursor.fetchall()}
    if "notes_model_used" not in history_columns:
        cursor.execute("ALTER TABLE history ADD COLUMN notes_model_used TEXT DEFAULT ''")

    # NOWA TABELA: Pamięć czatu (Prawdziwa rozmowa)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS chat_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            record_id INTEGER NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            model_used TEXT DEFAULT '',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(record_id) REFERENCES history(id) ON DELETE CASCADE
        )
    ''')
    cursor.execute("PRAGMA table_info(chat_history)")
    chat_history_columns = {row[1] for row in cursor.fetchall()}
    if "model_used" not in chat_history_columns:
        cursor.execute("ALTER TABLE chat_history ADD COLUMN model_used TEXT DEFAULT ''")

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS ai_models (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider TEXT NOT NULL,
            model_type TEXT NOT NULL,
            display_name TEXT NOT NULL,
            model_id TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            is_default INTEGER NOT NULL DEFAULT 0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_ai_models_lookup
        ON ai_models(model_type, provider, enabled, is_default)
    ''')
    seed_default_ai_models(cursor)
    conn.commit()
    conn.close()

init_db()

if LOCAL_MODELS_ENABLED:
    print("Ładowanie modeli Whisper...")
    models = {
        "tiny": whisper.load_model("tiny"),
        "base": whisper.load_model("base"),
        "small": whisper.load_model("small")
    }
else:
    print("Pominięto ładowanie lokalnych modeli Whisper (--no-local-models).")
    models = {}

@app.route('/', methods=['GET', 'POST'])
def login():
    if 'user_email' in session:
        return redirect(url_for('test_page'))
        
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("SELECT email, first_name, last_name, password_hash FROM users WHERE email = ?", (email,))
        user = cursor.fetchone()
        conn.close()
        
        if user and check_password_hash(user[3], password):
            session['user_email'] = user[0]
            flash('Zalogowano pomyślnie!', 'success')
            return redirect(url_for('test_page'))
        else:
            flash('Błędny email lub hasło.', 'danger')
        
    return render_template('index.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if 'user_email' in session:
        return redirect(url_for('test_page'))

    if request.method == 'POST':
        email = request.form.get('email')
        first_name = request.form.get('first_name')
        last_name = request.form.get('last_name')
        password = request.form.get('password')
        
        email_regex = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        if not re.match(email_regex, email):
            flash('Podaj poprawny adres e-mail (np. nazwa@domena.pl).', 'danger')
            return render_template('rejestracja.html')
            
        if len(password) < 8:
            flash('Hasło musi mieć co najmniej 8 znaków.', 'danger')
            return render_template('rejestracja.html')
        if not any(char.isupper() for char in password):
            flash('Hasło musi zawierać co najmniej jedną wielką literę.', 'danger')
            return render_template('rejestracja.html')
        if not any(char.isdigit() for char in password):
            flash('Hasło musi zawierać co najmniej jedną cyfrę.', 'danger')
            return render_template('rejestracja.html')
        if not any(char in '!@#$%^&*(),.?":{}|<>' for char in password):
            flash('Hasło musi zawierać co najmniej jeden znak specjalny.', 'danger')
            return render_template('rejestracja.html')
        
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("SELECT email FROM users WHERE email = ?", (email,))
        existing_user = cursor.fetchone()
        
        if existing_user:
            flash('Ten adres email jest już zarejestrowany!', 'danger')
            conn.close()
        else:
            hashed_password = generate_password_hash(password)
            cursor.execute(
                "INSERT INTO users (email, first_name, last_name, password_hash) VALUES (?, ?, ?, ?)",
                (email, first_name, last_name, hashed_password)
            )
            conn.commit()
            conn.close()
            flash('Rejestracja zakończona sukcesem! Możesz się zalogować.', 'success')
            return redirect(url_for('login'))
        
    return render_template('rejestracja.html')

@app.route('/test-page')
def test_page():
    if 'user_email' not in session:
        flash('Brak dostępu. Musisz się najpierw zalogować!', 'danger')
        return redirect(url_for('login'))
        
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT email, first_name, last_name FROM users WHERE email = ?", (session['user_email'],))
    user = cursor.fetchone()
    conn.close()
    
    user_data = {
        'email': user[0],
        'first_name': user[1],
        'last_name': user[2]
    }
    cloud_models = list_ai_models(model_type="transcription", enabled_only=True)
    default_cloud_model = get_default_ai_model("transcription")
    default_cloud_model_id = default_cloud_model["id"] if default_cloud_model else None
    default_notes_model = get_default_ai_model("notes")
    openai_chat_models = list_available_openai_chat_models()
    for model in openai_chat_models:
        model["chat_label"] = describe_chat_answer_model(model)
    default_chat_model = get_default_openai_chat_model()
    initial_notes_model_label = (
        describe_notes_model(default_notes_model, "online")
        if not LOCAL_MODELS_ENABLED
        else describe_local_notes_model()
    )
    return render_template(
        'test-page.html',
        user=user_data,
        cloud_models=cloud_models,
        default_cloud_model_id=default_cloud_model_id,
        initial_notes_model_label=initial_notes_model_label,
        default_cloud_notes_model_label=describe_notes_model(default_notes_model, "online"),
        local_notes_model_label=describe_local_notes_model(),
        openai_chat_models=openai_chat_models,
        default_chat_model_id=default_chat_model["id"] if default_chat_model else None,
        default_chat_model_label=describe_chat_answer_model(default_chat_model) if default_chat_model else "Brak dostępnego modelu OpenAI chat",
        local_models_enabled=LOCAL_MODELS_ENABLED
    )

@app.route('/settings', methods=['GET'])
def settings():
    if 'user_email' not in session:
        flash('Brak dostępu. Musisz się najpierw zalogować!', 'danger')
        return redirect(url_for('login'))

    api_key_status = {
        provider: {
            "label": config["label"],
            "env_key": config["env_key"],
            "configured": bool(get_provider_api_key(provider))
        }
        for provider, config in PROVIDERS.items()
    }

    return render_template(
        'settings.html',
        models=list_ai_models(),
        providers=PROVIDERS,
        model_types=MODEL_TYPES,
        model_catalog=MODEL_CATALOG,
        local_model_requirements=LOCAL_MODEL_REQUIREMENTS,
        api_key_status=api_key_status
    )

@app.route('/settings/models', methods=['POST'])
def add_model():
    if 'user_email' not in session:
        flash('Brak dostępu. Musisz się najpierw zalogować!', 'danger')
        return redirect(url_for('login'))

    provider = request.form.get('provider', '').strip().lower()
    model_type = request.form.get('model_type', '').strip().lower()
    display_name = request.form.get('display_name', '').strip()
    model_id = request.form.get('model_id', '').strip()
    enabled = 1 if request.form.get('enabled') == 'on' else 0
    is_default = 1 if request.form.get('is_default') == 'on' else 0

    if not enabled:
        is_default = 0

    if provider not in PROVIDERS or model_type not in MODEL_TYPES or not display_name or not model_id:
        flash('Uzupełnij poprawnie providera, typ, nazwę oraz identyfikator modelu.', 'danger')
        return redirect(url_for('settings'))

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    if is_default:
        cursor.execute(
            "UPDATE ai_models SET is_default = 0 WHERE provider = ? AND model_type = ?",
            (provider, model_type)
        )

    cursor.execute(
        """
        INSERT INTO ai_models (provider, model_type, display_name, model_id, enabled, is_default)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (provider, model_type, display_name, model_id, enabled, is_default)
    )
    conn.commit()
    conn.close()

    flash('Model został dodany.', 'success')
    return redirect(url_for('settings'))

@app.route('/settings/models/<int:model_pk>', methods=['POST'])
def update_model(model_pk):
    if 'user_email' not in session:
        flash('Brak dostępu. Musisz się najpierw zalogować!', 'danger')
        return redirect(url_for('login'))

    provider = request.form.get('provider', '').strip().lower()
    model_type = request.form.get('model_type', '').strip().lower()
    display_name = request.form.get('display_name', '').strip()
    model_id = request.form.get('model_id', '').strip()
    enabled = 1 if request.form.get('enabled') == 'on' else 0
    is_default = 1 if request.form.get('is_default') == 'on' else 0

    if display_name.lower() == "usun model":
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("SELECT provider, model_type, display_name, is_default FROM ai_models WHERE id = ?", (model_pk,))
        model_row = cursor.fetchone()
        if not model_row:
            conn.close()
            flash('Nie znaleziono modelu do usunięcia.', 'danger')
            return redirect(url_for('settings'))

        deleted_provider, deleted_model_type, deleted_display_name, was_default = model_row
        cursor.execute("DELETE FROM ai_models WHERE id = ?", (model_pk,))
        if was_default:
            promote_first_enabled_model_as_default(cursor, deleted_provider, deleted_model_type)

        conn.commit()
        conn.close()
        flash(f"Model '{deleted_display_name}' został usunięty.", 'success')
        return redirect(url_for('settings'))

    if not enabled:
        is_default = 0

    if provider not in PROVIDERS or model_type not in MODEL_TYPES or not display_name or not model_id:
        flash('Nie zapisano zmian. Sprawdź dane modelu.', 'danger')
        return redirect(url_for('settings'))

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    if is_default:
        cursor.execute(
            "UPDATE ai_models SET is_default = 0 WHERE provider = ? AND model_type = ? AND id != ?",
            (provider, model_type, model_pk)
        )

    cursor.execute(
        """
        UPDATE ai_models
        SET provider = ?,
            model_type = ?,
            display_name = ?,
            model_id = ?,
            enabled = ?,
            is_default = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (provider, model_type, display_name, model_id, enabled, is_default, model_pk)
    )
    conn.commit()
    conn.close()

    flash('Model został zaktualizowany.', 'success')
    return redirect(url_for('settings'))

@app.route('/transcribe', methods=['POST'])
def transcribe():
    if 'user_email' not in session:
        return jsonify({"error": "Brak autoryzacji"}), 401
        
    youtube_url = request.form.get('youtube_url', '').strip()
    processing_mode = request.form.get('processing_mode', get_default_processing_mode())
    model_name = request.form.get('model_name', 'base')
    cloud_model_id = request.form.get('cloud_model_id')
    language = request.form.get('language', 'auto')
    task = request.form.get('task', 'transcribe')
    custom_name = request.form.get('custom_name', '').strip()
    
    file_path = None
    display_title = ""
    notes_model_used = ""

    try:
        if youtube_url:
            youtube_download = download_youtube_audio(youtube_url)
            file_path = youtube_download["file_path"]
            display_title = custom_name if custom_name else f"YT: {youtube_download['title']}"
        else:
            if 'file' not in request.files:
                return jsonify({"error": "Brak pliku lub linku YouTube"}), 400
            file = request.files['file']
            if file.filename == '':
                return jsonify({"error": "Nie wybrano pliku"}), 400
                
            file_path = make_temp_upload_path(file.filename)
            file.save(file_path)
            display_title = custom_name if custom_name else file.filename

        # Jeśli przesłano gotowy plik tekstowy .txt
        if file_path.endswith('.txt') and not youtube_url:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                surowy_tekst = f.read()
            if os.path.exists(file_path):
                os.remove(file_path)
                
            detected_lang = "Plik tekstowy"
            model_used_info = "Czysty tekst (Brak STT)"
            notes_model_used = describe_notes_model(None, processing_mode)
            
            if processing_mode == 'online':
                selected_transcription_model = get_selected_transcription_model(cloud_model_id)
                preferred_provider = selected_transcription_model["provider"] if selected_transcription_model else None
                notatki_ai, notes_model = generate_audio_notes(
                    surowy_tekst,
                    processing_mode,
                    preferred_provider=preferred_provider
                )
                notes_model_used = describe_notes_model(notes_model, processing_mode)
                model_used_info = f"Plik tekstowy + notatki: {describe_cloud_model(notes_model)}"
            else:
                try:
                    prompt = build_audio_notes_prompt(surowy_tekst)
                    response = ollama.chat(model='llama3', messages=[{'role': 'user', 'content': prompt}])
                    notatki_ai = response['message']['content']
                except:
                    notatki_ai = "Nie udało się wygenerować notatek AI lokalnie. Upewnij się, że Ollama działa w tle."
        else:
            # Klasyczne przetwarzanie audio STT
            transcription_result = process_audio_transcription(
                file_path,
                processing_mode=processing_mode,
                model_name=model_name,
                cloud_model_id=cloud_model_id,
                language=language,
                task=task
            )
            surowy_tekst = transcription_result["text"]
            notatki_ai = transcription_result["notes"]
            notes_model_used = transcription_result.get("notes_model_used", "")
            detected_lang = transcription_result["language"]
            task = transcription_result["task"]
            model_used_info = transcription_result["model_used"]

            if os.path.exists(file_path):
                os.remove(file_path)

        new_id = save_transcription_history(session['user_email'], display_title, surowy_tekst, notatki_ai, notes_model_used)

        return jsonify({
            "text": surowy_tekst, 
            "notes": notatki_ai,
            "notes_model_used": notes_model_used,
            "model_used": model_used_info,
            "language": detected_lang,
            "task": task,
            "saved_name": display_title,
            "record_id": new_id
        })

    except ValueError as e:
        if file_path and os.path.exists(file_path):
            os.remove(file_path)
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        if file_path and os.path.exists(file_path):
            os.remove(file_path)
        return jsonify({"error": str(e)}), 500

@app.route('/api/models', methods=['GET'])
def api_models():
    model_type = (request.args.get("type") or request.args.get("model_type") or "").strip().lower() or None
    if model_type and model_type not in MODEL_TYPES:
        allowed_types = "', '".join(MODEL_TYPES.keys())
        return jsonify({"error": f"Nieobsługiwany typ modelu. Użyj jednego z: '{allowed_types}'."}), 400

    include_disabled = parse_bool_setting(request.args.get("include_disabled"), default=False)

    local_models = build_local_model_list(model_type=model_type)
    cloud_models = build_cloud_model_list(model_type=model_type, include_disabled=include_disabled)

    return jsonify({
        "local": local_models,
        "cloud": cloud_models,
        "models": local_models + cloud_models,
        "filters": {
            "type": model_type,
            "include_disabled": include_disabled
        },
        "counts": {
            "local": len(local_models),
            "cloud": len(cloud_models),
            "total": len(local_models) + len(cloud_models)
        }
    })

@app.route('/api/youtube/transcribe', methods=['POST'])
def api_youtube_transcribe():
    if 'user_email' not in session:
        return jsonify({"error": "Brak autoryzacji"}), 401

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "Oczekiwano danych JSON"}), 400

    settings = payload.get("settings") or {}
    if not isinstance(settings, dict):
        return jsonify({"error": "Pole settings musi być obiektem JSON"}), 400

    yt_url_value = (
        payload.get("yt_url")
        or payload.get("youtube_url")
        or settings.get("yt_url")
        or settings.get("youtube_url")
        or ""
    )
    yt_url = str(yt_url_value).strip()
    if not yt_url:
        return jsonify({"error": "Brak wymaganego pola yt_url"}), 400

    processing_mode = get_payload_setting(payload, settings, "processing_mode", get_default_processing_mode())
    model_name = get_payload_setting(payload, settings, "model_name", "base")
    cloud_model_id = get_payload_setting(payload, settings, "cloud_model_id")
    language = get_payload_setting(payload, settings, "language", "auto")
    task = get_payload_setting(payload, settings, "task", "transcribe")
    custom_name = str(get_payload_setting(payload, settings, "custom_name", "") or "").strip()
    save_to_history = parse_bool_setting(get_payload_setting(payload, settings, "save_to_history"), default=False)

    file_path = None

    try:
        youtube_download = download_youtube_audio(yt_url)
        file_path = youtube_download["file_path"]
        transcription_result = process_audio_transcription(
            file_path,
            processing_mode=processing_mode,
            model_name=model_name,
            cloud_model_id=cloud_model_id,
            language=language,
            task=task
        )

        if os.path.exists(file_path):
            os.remove(file_path)

        saved_name = custom_name if custom_name else f"YT: {youtube_download['title']}"
        record_id = None
        if save_to_history:
            record_id = save_transcription_history(
                session['user_email'],
                saved_name,
                transcription_result["text"],
                transcription_result["notes"],
                transcription_result.get("notes_model_used", "")
            )

        return jsonify({
            "text": transcription_result["text"],
            "summary": transcription_result["notes"],
            "notes": transcription_result["notes"],
            "language": transcription_result["language"],
            "model_used": transcription_result["model_used"],
            "notes_model_used": transcription_result.get("notes_model_used", ""),
            "task": transcription_result["task"],
            "saved": record_id is not None,
            "saved_name": saved_name,
            "record_id": record_id,
            "youtube": {
                "id": youtube_download["id"],
                "title": youtube_download["title"],
                "duration": youtube_download["duration"],
                "url": youtube_download["webpage_url"]
            }
        })

    except ValueError as e:
        if file_path and os.path.exists(file_path):
            os.remove(file_path)
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        if file_path and os.path.exists(file_path):
            os.remove(file_path)
        return jsonify({"error": str(e)}), 500

@app.route('/ask-question', methods=['POST'])
def ask_question():
    user_email = session.get('user_email')
    if not user_email:
        app.logger.warning("ask-question: unauthorized request from %s", request.remote_addr)
        return jsonify({"error": "Brak autoryzacji"}), 401
        
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        app.logger.warning(
            "ask-question: invalid json body user=%s body_type=%s",
            user_email,
            type(data).__name__
        )
        return jsonify({"error": "Nieprawidłowy JSON"}), 400

    record_id = data.get('id')
    question = str(data.get('question') or '').strip()
    app.logger.info(
        "ask-question: received user=%s record_id=%s question_len=%s payload_keys=%s",
        user_email,
        record_id,
        len(question),
        list(data.keys())
    )
    
    if not record_id or not question:
        app.logger.warning(
            "ask-question: invalid payload user=%s record_id=%s question_len=%s",
            user_email,
            record_id,
            len(question)
        )
        return jsonify({"error": "Brak ID nagrania lub pytania"}), 400
        
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # 1. Sprawdzenie uprawnień i pobranie tekstu źródłowego
    cursor.execute("SELECT raw_text FROM history WHERE id = ? AND user_email = ?", (record_id, user_email))
    row = cursor.fetchone()
    
    if not row:
        conn.close()
        app.logger.warning(
            "ask-question: record not found user=%s record_id=%s",
            user_email,
            record_id
        )
        return jsonify({"error": "Nie znaleziono nagrania w Twojej historii"}), 404
        
    transkrypcja = row[0]
    app.logger.info(
        "ask-question: record loaded user=%s record_id=%s transcript_len=%s",
        user_email,
        record_id,
        len(transkrypcja or "")
    )
    
    # 2. Pobranie historii czatu (ostatnie 6 wiadomości chronologicznie)
    cursor.execute(
        "SELECT role, content FROM (SELECT role, content, id FROM chat_history WHERE record_id = ? ORDER BY id DESC LIMIT 6) ORDER BY id ASC", 
        (record_id,)
    )
    context_rows = cursor.fetchall()
    conn.close()
    app.logger.info(
        "ask-question: context loaded record_id=%s context_count=%s",
        record_id,
        len(context_rows)
    )
    
    try:
        # 4. Zapytanie do niezależnego modelu czatu. OpenAI używa Responses API z web_search.
        yt_response_prompt = build_web_search_question_prompt(transkrypcja, context_rows, question)
        chat_model = get_default_ai_model("chat")
        app.logger.info(
            "ask-question: ai request start record_id=%s provider=%s model=%s prompt_len=%s",
            record_id,
            chat_model.get("provider") if chat_model else None,
            chat_model.get("model_id") if chat_model else None,
            len(yt_response_prompt)
        )
        ai_answer = answer_question_with_ai(yt_response_prompt, chat_model)
        odpowiedz_ai = ai_answer["text"]
        app.logger.info(
            "ask-question: ai request ok record_id=%s model=%s answer_len=%s sources_count=%s",
            record_id,
            ai_answer.get("model"),
            len(odpowiedz_ai or ""),
            len(ai_answer.get("sources", []))
        )
        
        # 5. Zapisanie aktualnego pytania oraz odpowiedzi do bazy (pamięć trwała)
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("INSERT INTO chat_history (record_id, role, content) VALUES (?, ?, ?)", (record_id, 'user', question))
        cursor.execute(
            "INSERT INTO chat_history (record_id, role, content, model_used) VALUES (?, ?, ?, ?)",
            (record_id, 'assistant', odpowiedz_ai, ai_answer["engine"])
        )
        conn.commit()
        conn.close()
        app.logger.info("ask-question: saved chat history record_id=%s", record_id)
        
        return jsonify({
            "answer": odpowiedz_ai,
            "engine": ai_answer["engine"],
            "chat_model_used": ai_answer["engine"],
            "sources": ai_answer["sources"]
        })
    except Exception as e:
        app.logger.exception(
            "ask-question: error user=%s record_id=%s question_len=%s",
            user_email,
            record_id,
            len(question)
        )
        return jsonify({"error": f"Błąd AI: {str(e)}"}), 500

@app.route('/get-history', methods=['GET'])
def get_history():
    if 'user_email' not in session:
        return jsonify({"error": "Brak autoryzacji"}), 401
        
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id, filename, raw_text, ai_notes, COALESCE(notes_model_used, ''), datetime(created_at, 'localtime')
        FROM history
        WHERE user_email = ?
        ORDER BY created_at DESC
        """,
        (session['user_email'],)
    )
    rows = cursor.fetchall()
    cursor.execute(
        """
        SELECT ch.record_id, ch.role, ch.content, datetime(ch.created_at, 'localtime'), COALESCE(ch.model_used, '')
        FROM chat_history ch
        JOIN history h ON h.id = ch.record_id
        WHERE h.user_email = ?
        ORDER BY ch.id ASC
        """,
        (session['user_email'],)
    )
    chat_rows = cursor.fetchall()
    conn.close()

    chat_by_record = {}
    for record_id, role, content, created_at, model_used in chat_rows:
        chat_by_record.setdefault(record_id, []).append({
            "role": role,
            "content": content,
            "created_at": created_at,
            "engine": model_used
        })
    
    history_list = []
    for r in rows:
        chat_messages = chat_by_record.get(r[0], [])
        history_list.append({
            "id": r[0],
            "filename": r[1],
            "raw_text": r[2],
            "ai_notes": r[3],
            "notes_model_used": r[4],
            "created_at": r[5],
            "chat_messages": chat_messages,
            "chat_count": len(chat_messages)
        })
    return jsonify(history_list)

@app.route('/delete-history/<int:item_id>', methods=['DELETE'])
def delete_history(item_id):
    if 'user_email' not in session:
        return jsonify({"error": "Brak autoryzacji"}), 401
        
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM history WHERE id = ? AND user_email = ?", (item_id, session['user_email']))
    conn.commit()
    conn.close()
    return jsonify({"success": True})

@app.route('/export/docx', methods=['POST'])
def export_docx():
    if 'user_email' not in session:
        return "Brak autoryzacji", 401
    
    content = request.form.get('content', '')
    title = request.form.get('title', 'Dokument')
    
    doc = Document()
    doc.add_heading(title, 0)
    
    for line in content.split('\n'):
        doc.add_paragraph(line)
        
    file_stream = io.BytesIO()
    doc.save(file_stream)
    file_stream.seek(0)
    
    return send_file(
        file_stream,
        mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        as_attachment=True,
        download_name=f'{title}.docx'
    )

@app.route('/export/pdf', methods=['POST'])
def export_pdf():
    if 'user_email' not in session:
        return "Brak autoryzacji", 401
        
    content = request.form.get('content', '')
    title = request.form.get('title', 'Dokument')
    
    file_stream = io.BytesIO()
    pdf = canvas.Canvas(file_stream, pagesize=letter)
    
    try:
        sys_os = platform.system()
        if sys_os == "Windows":
            font_path = "C:\\Windows\\Fonts\\arial.ttf"
            font_path_bold = "C:\\Windows\\Fonts\\arialbd.ttf"
        elif sys_os == "Darwin":
            font_path = "/Library/Fonts/Arial.ttf"
            font_path_bold = "/Library/Fonts/Arial Bold.ttf"
        else:
            font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
            font_path_bold = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

        pdfmetrics.registerFont(TTFont('PolishArial', font_path))
        pdfmetrics.registerFont(TTFont('PolishArial-Bold', font_path_bold))
        font_regular = 'PolishArial'
        font_bold = 'PolishArial-Bold'
    except:
        font_regular = 'Helvetica'
        font_bold = 'Helvetica-Bold'

    pdf.setTitle(title)
    
    pdf.setFont(font_bold, 16)
    pdf.drawString(50, 750, title)
    pdf.setStrokeColorRGB(0.2, 0.2, 0.2)
    pdf.line(50, 740, 550, 740)
    
    pdf.setFont(font_regular, 10)
    y = 710
    for line in content.split('\n'):
        clean_line = line.encode('utf-8', errors='ignore').decode('utf-8')
        wrapped_lines = simpleSplit(clean_line, font_regular, 10, 500) or [""]
        for wrapped_line in wrapped_lines:
            if y < 50:
                pdf.showPage()
                y = 750
                pdf.setFont(font_regular, 10)
            pdf.drawString(50, y, wrapped_line)
            y -= 15

        if not clean_line.strip():
            y -= 5

        if y < 50:
            pdf.showPage()
            y = 750
            pdf.setFont(font_regular, 10)
        
    pdf.save()
    file_stream.seek(0)
    
    return send_file(
        file_stream,
        mimetype='application/pdf',
        as_attachment=True,
        download_name=f'{title}.pdf'
    )

@app.route('/change-password', methods=['GET', 'POST'])
def change_password():
    if 'user_email' not in session:
        flash('Musisz się zalogować, aby zmienić hasło.', 'danger')
        return redirect(url_for('login'))
        
    if request.method == 'POST':
        current_password = request.form.get('current_password')
        new_password = request.form.get('new_password')
        confirm_new_password = request.form.get('confirm_new_password')
        
        email = session['user_email']
        
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("SELECT password_hash FROM users WHERE email = ?", (email,))
        user = cursor.fetchone()
        
        if not user or not check_password_hash(user[0], current_password):
            flash('Aktualne hasło jest niepoprawne.', 'danger')
            conn.close()
        elif new_password != confirm_new_password:
            flash('Nowe hasła nie są identyczne.', 'danger')
            conn.close()
        else:
            new_hashed = generate_password_hash(new_password)
            cursor.execute("UPDATE users SET password_hash = ? WHERE email = ?", (new_hashed, email))
            conn.commit()
            conn.close()
            flash('Hasło zostało pomyślnie zmienione!', 'success')
            return redirect(url_for('test_page'))
            
    return render_template('zmiana-hasla.html')

@app.route('/logout')
def logout():
    session.pop('user_email', None)
    flash('Wylogowano pomyślnie.', 'success')
    return redirect(url_for('login'))

if __name__ == '__main__':
    server_host = '0.0.0.0'
    server_port = get_server_port(host=server_host, default_port=8000)
    app.run(debug=True, host=server_host, port=server_port)
