"""Shared Gemini setup (google-genai): one pinned fast model, one client, and the on/off switches.

AI is advisory in BatchWatch: every caller must treat a failure as "no opinion", never as an error.
"""
import os

from dotenv import load_dotenv
from google import genai
from google.genai import types

DEFAULT_MODEL = "gemini-3.5-flash-lite"  # pinned: the 'gemini-flash-latest' alias took 79 s to say "OK"
TIMEOUT_MS = 10000


def model() -> str:
    """Model id; override with GEMINI_MODEL when Google retires this one."""
    return os.getenv("GEMINI_MODEL") or DEFAULT_MODEL


def switched_off() -> bool:
    """True when AI is disabled (BATCHWATCH_AI=off) or the demo is offline (BATCHWATCH_OFFLINE=1)."""
    return os.getenv("BATCHWATCH_AI") == "off" or os.getenv("BATCHWATCH_OFFLINE") == "1"


def has_key() -> bool:
    """True if GEMINI_API_KEY is set (the .env file is loaded first)."""
    load_dotenv()
    return bool(os.getenv("GEMINI_API_KEY"))


def get_client() -> genai.Client:
    """Client with a 10 s request timeout; the SDK reads GEMINI_API_KEY from the environment."""
    load_dotenv()
    return genai.Client(http_options=types.HttpOptions(timeout=TIMEOUT_MS))
