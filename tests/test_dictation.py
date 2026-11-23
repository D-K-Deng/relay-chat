import io
import json
import tempfile
import unittest
import urllib.error
import urllib.request
import wave
from contextlib import ExitStack
from email.parser import BytesParser
from email.policy import default
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from relay import dictation
from relay.server import RelayServer
from test_relay import serving


class AudioFixture(BaseHTTPRequestHandler):
    def do_POST(self):
        content = self.rfile.read(int(self.headers["Content-Length"]))
        envelope = f"Content-Type: {self.headers['Content-Type']}\r\n\r\n".encode() + content
        message = BytesParser(policy=default).parsebytes(envelope)
        parts = {part.get_param("name", header="content-disposition"): part for part in message.iter_parts()}
        self.server.calls.append((self.path, self.headers["Authorization"], parts))
        self.send_response(self.server.status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(self.server.answer).encode())

    def log_message(self, *args):
        pass


def wav_clip():
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setparams((1, 2, 8000, 0, "NONE", ""))
        audio.writeframes(bytes(8000 * 2))
    return output.getvalue()


class DictationFlow(unittest.TestCase):
    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        directory = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        self.audio = ThreadingHTTPServer(("127.0.0.1", 0), AudioFixture)
        self.audio.calls = []
        self.audio.status = 200
        self.audio.answer = {"text": "  继续开发 Relay。  ", "usage": {"type": "duration", "seconds": 1}}
        host = stack.enter_context(serving(self.audio))
        stack.enter_context(patch.object(dictation, "ENDPOINT", host + "/v1/audio/transcriptions"))
        self.app = RelayServer(0, directory / "data", key_source=lambda: ("audio-fixture", "test"),
                               codex_home=directory / "codex")
        self.url = stack.enter_context(serving(self.app))
        self.app.store.set_balance(1)

    def upload(self, mime, clip):
        body = urllib.request.Request(self.url + "/api/transcribe", data=clip, headers={"Content-Type": mime})
        with urllib.request.urlopen(body, timeout=5) as response:
            return json.load(response)

    def test_audio_upload_replies_with_draft_and_counts_duration(self):
        clip = wav_clip()
        self.assertEqual(self.upload("audio/wav", clip), {"text": "继续开发 Relay。", "seconds": 1})
        path, authorization, parts = self.audio.calls[0]
        self.assertEqual((path, authorization), ("/v1/audio/transcriptions", "Bearer audio-fixture"))
        self.assertEqual(parts["model"].get_content(), "gpt-transcribe")
        self.assertEqual(parts["file"].get_filename(), "dictation.wav")
        self.assertEqual(parts["file"].get_content_type(), "audio/wav")
        self.assertEqual(parts["file"].get_payload(decode=True), clip)
        self.assertEqual(self.app.store.list_chats(), [])
        self.assertAlmostEqual(self.app.store.balance()["meter"]["spent"], .0045 / 60)
        self.assertEqual(self.app.store.balance()["meter"]["priced"], 1)

    def test_unsupported_and_rejected_clips_never_create_a_draft_or_charge_estimate(self):
        with self.assertRaises(urllib.error.HTTPError) as unsupported:
            self.upload("text/plain", b"not recorded audio")
        self.assertEqual(unsupported.exception.code, 400)
        self.assertFalse(self.audio.calls)
        self.audio.status = 401
        self.audio.answer = {"error": {"code": "invalid_api_key"}}
        with self.assertRaises(urllib.error.HTTPError) as rejected:
            self.upload("audio/webm;codecs=opus", b"webm fixture")
        self.assertIn("API key", json.load(rejected.exception)["error"])
        self.assertEqual(self.app.store.balance()["meter"]["priced"], 0)
        self.assertEqual(self.app.store.list_chats(), [])
