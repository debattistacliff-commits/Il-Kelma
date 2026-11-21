"""The local HTTP interface."""
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
from threading import Thread
from urllib.parse import parse_qs, urlsplit

from ilkelma.languages import packs

LIMIT = 25 * 1024 * 1024


def server_for(engine, port=3420, *, online_voices=lambda: True):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def reply(self, status, body, content_type="application/json"):
            if content_type == "application/json":
                body = json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def local(self):
            host = self.headers.get("Host", "").split(":")[0]
            if host in ("127.0.0.1", "localhost") and not self.headers.get("Origin"):
                return True
            self.reply(403, {"error": "Use a local HTTP client."})
            return False

        def do_GET(self):
            if not self.local():
                return
            if self.path == "/health":
                self.reply(200, {"status": "ok", "version": "0.2"})
            else:
                self.reply(404, {"error": "Route not found."})

        def do_POST(self):
            if not self.local():
                return
            route = urlsplit(self.path)
            if route.path not in ("/transcribe", "/fix", "/speak"):
                self.reply(404, {"error": "Route not found."})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0:
                    raise ValueError("Send a request body.")
                if length > LIMIT:
                    self.reply(413, {"error": "Use a file below 25 MB."})
                    return
                self.connection.settimeout(15)
                body = self.rfile.read(length)
                if len(body) != length:
                    raise ValueError("The request body is incomplete.")
                language = parse_qs(route.query).get("lang", [None])[0]
                if route.path == "/transcribe":
                    content_type = self.headers.get_content_type()
                    if content_type == "multipart/form-data":
                        message = BytesParser(policy=policy.default).parsebytes(
                            f"Content-Type: {self.headers['Content-Type']}\r\n\r\n".encode() + body)
                        body = next((part.get_payload(decode=True) for part in message.iter_parts()
                                     if part.get_param("name", header="content-disposition") == "file"), b"")
                    elif not (content_type.startswith("audio/") or content_type == "application/octet-stream"):
                        raise ValueError("Send audio bytes or a multipart file field.")
                    if not body:
                        raise ValueError("Send an audio file.")
                else:
                    if self.headers.get_content_type() != "application/json":
                        raise ValueError("Send application/json.")
                    data = json.loads(body)
                    if not isinstance(data, dict) or not isinstance(data.get("text"), str):
                        raise ValueError("Send a text field.")
                    text = data["text"]
                    language = data.get("lang", language)
                    if not text.strip() or len(text) > 12000:
                        raise ValueError("Use 1 to 12,000 characters of text.")
                if language is not None and (not isinstance(language, str) or language not in packs()):
                    raise ValueError("Unknown language pack.")
                if route.path == "/fix":
                    self.reply(200, {"text": engine.fix(text)})
                elif route.path == "/transcribe":
                    self.reply(200, engine.transcribe(body, language))
                elif online_voices():
                    self.reply(200, engine.speak(text, language), "audio/mpeg")
                else:
                    self.reply(503, {"error": "Online voices are off."})
            except (ValueError, TimeoutError) as error:
                self.reply(400, {"error": str(error)})
            except (BrokenPipeError, ConnectionResetError):
                return
            except Exception:
                logging.getLogger(__name__).exception("API operation failed")
                self.reply(500, {"error": "Il-Kelma could not finish the request."})

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def start(engine, port=3420, **options):
    server = server_for(engine, port, **options)
    Thread(target=server.serve_forever, daemon=True).start()
    return server
