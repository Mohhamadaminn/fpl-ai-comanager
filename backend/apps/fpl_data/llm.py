import httpx
from django.conf import settings
from groq import Groq


def make_groq_client() -> Groq:
    """Groq client with explicit limits.

    The SDK defaults (60 s timeout, 2 retries) let one stuck call hold a web thread or a
    Celery worker for minutes. The values come from GROQ_TIMEOUT_SECONDS and
    GROQ_MAX_RETRIES in settings.
    """
    return Groq(
        api_key=settings.GROQ_API_KEY,
        timeout=httpx.Timeout(settings.GROQ_TIMEOUT_SECONDS, connect=5.0),
        max_retries=settings.GROQ_MAX_RETRIES,
    )
