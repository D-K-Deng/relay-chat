"""Transcribe one microphone recording without saving the audio on disk."""
import json
import urllib.error
import urllib.request
from uuid import uuid4

from .provider import explain


MODEL = "gpt-transcribe"
ENDPOINT = "https://api.openai.com/v1/audio/transcriptions"
MAX_AUDIO = 25_000_000
EXTENSIONS = {"audio/webm": "webm", "audio/mp4": "mp4", "audio/ogg": "ogg", "audio/wav": "wav"}


def transcribe(key, audio, mime):
    boundary = uuid4().hex
    head = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"model\"\r\n\r\n{MODEL}\r\n"
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"dictation.{EXTENSIONS[mime]}\"\r\n"
            f"Content-Type: {mime}\r\n\r\n").encode()
    request = urllib.request.Request(ENDPOINT, data=head + audio + f"\r\n--{boundary}--\r\n".encode(),
        headers={"Authorization": "Bearer " + key, "Content-Type": "multipart/form-data; boundary=" + boundary})
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        try:
            detail = json.loads(error.read()).get("error", {})
        except ValueError:
            detail = {}
        raise ValueError(explain(detail, error.code)) from None
    except OSError:
        raise ValueError("Could not reach OpenAI for transcription. Check your connection and try again.") from None


def handle(handler, key):
    if not key:
        raise ValueError("Connect your API key to use dictation.")
    mime = handler.headers.get_content_type()
    size = int(handler.headers.get("Content-Length", 0))
    if mime not in EXTENSIONS or not 0 < size <= MAX_AUDIO:
        raise ValueError("Record a WebM, MP4, Ogg or WAV audio clip up to 25 MB.")
    ticket = handler.server.store.meter_ticket(MODEL)
    result = transcribe(key, handler.rfile.read(size), mime)
    usage = result.get("usage") or {}
    handler.server.store.record_usage(ticket, {"usage": usage})
    text = result.get("text", "").strip()
    if not text:
        raise ValueError("No speech was transcribed. Try recording again.")
    return {"text": text, "seconds": usage.get("seconds")}
