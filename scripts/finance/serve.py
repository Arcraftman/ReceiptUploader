"""Loopback-only finance bridge. Use SSH forwarding from Windows."""
import argparse
import hmac
import json
import os
from pathlib import Path
import secrets
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlsplit, parse_qs

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from kdzwy_receipt_uploader.finance_snapshot import collect_snapshot, spreadsheet_xml
from kdzwy_receipt_uploader.read_api_checks import CheckFailure


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=18765)
    args = parser.parse_args()
    directory = ROOT / "runtime/finance"
    directory.mkdir(parents=True, exist_ok=True)
    token_file = directory / "access.token"
    if not token_file.exists():
        fd = os.open(token_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w") as handle:
            handle.write(secrets.token_urlsafe(32))
    token_file.chmod(0o600)
    token = token_file.read_text().strip()
    if len(token) < 32:
        raise ValueError("本地访问令牌无效")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, code, body, content_type="application/json; charset=utf-8"):
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if not hmac.compare_digest(self.headers.get("Authorization", ""), "Bearer " + token):
                self.send(401, b'{"error":"Unauthorized"}')
                return
            url = urlsplit(self.path)
            if url.path == "/health":
                self.send(200, b'{"schema":"1","readOnly":true}')
                return
            if url.path != "/snapshot":
                self.send(404, b'{"error":"Not found"}')
                return
            query = parse_qs(url.query)
            if set(query) != {"company", "month"} or any(len(v) != 1 for v in query.values()):
                self.send(400, b'{"error":"Expected company and month"}')
                return
            try:
                snapshot = collect_snapshot(ROOT, query["company"][0], query["month"][0])
                body = spreadsheet_xml(snapshot)
                self.send(200, body, "application/xml; charset=utf-8")
            except (CheckFailure, ValueError) as exc:
                self.send(422, json.dumps({"error": str(exc)}, ensure_ascii=False).encode())
            except Exception:
                self.send(502, b'{"error":"Read failed; previous workbook data is unchanged"}')

    print(f"只读财务服务：http://127.0.0.1:{args.port}；令牌文件：{token_file}", flush=True)
    HTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
