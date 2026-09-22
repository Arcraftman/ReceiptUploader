"""Loopback-only finance bridge. Use SSH forwarding from Windows."""
import argparse
import hmac
import json
import os
from pathlib import Path
import secrets
import sys
import socket
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlsplit, parse_qs

from kdzwy_receipt_uploader.project_runtime import project_root
from kdzwy_receipt_uploader.finance.snapshot import SCHEMA_VERSION, collect_snapshot, spreadsheet_xml
from kdzwy_receipt_uploader.integrations.read_client import CheckFailure
from .interpretation import interpret, interpretation_xml


class FinanceHTTPServer(HTTPServer):
    allow_reuse_address = False

    def server_bind(self):
        if sys.platform == 'win32':
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=18765)
    args = parser.parse_args(argv)
    directory = project_root() / "runtime/finance"
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
                self.send(200, json.dumps({"schema": SCHEMA_VERSION, "readOnly": True}).encode())
                return
            if url.path != "/snapshot":
                self.send(404, b'{"error":"Not found"}')
                return
            query = parse_qs(url.query)
            if set(query) != {"company", "month"} or any(len(v) != 1 for v in query.values()):
                self.send(400, b'{"error":"Expected company and month"}')
                return
            try:
                snapshot = collect_snapshot(project_root(), query["company"][0], query["month"][0])
                body = spreadsheet_xml(snapshot)
                self.send(200, body, "application/xml; charset=utf-8")
            except (CheckFailure, ValueError) as exc:
                self.send(422, json.dumps({"error": str(exc)}, ensure_ascii=False).encode())
            except Exception:
                self.send(502, b'{"error":"Read failed; previous workbook data is unchanged"}')

        def do_POST(self):
            if not hmac.compare_digest(self.headers.get('Authorization',''), 'Bearer '+token):
                self.send(401,b'Unauthorized')
                return
            if self.path != '/interpretations':
                self.send(404,b'Not found')
                return
            try:
                size = int(self.headers.get('Content-Length','0'))
                if not 0 < size <= 32000:
                    raise ValueError('解读数据大小无效')
                payload = json.loads(self.rfile.read(size).decode('utf-8'))
                result = interpret(project_root(),payload)
                self.send(200,interpretation_xml(result),'application/xml; charset=utf-8')
            except (ValueError,OSError) as exc:
                self.send(422,str(exc).encode('utf-8'),'text/plain; charset=utf-8')
            except Exception:
                self.send(502,'解读生成失败，财务数据已保留'.encode('utf-8'),'text/plain; charset=utf-8')

    server = FinanceHTTPServer(("127.0.0.1", args.port), Handler)
    pid_file = directory / "service.pid"
    pid_file.write_text(str(os.getpid()), encoding="ascii")
    print(f"只读财务服务：http://127.0.0.1:{args.port}；令牌文件：{token_file}", flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        if pid_file.exists() and pid_file.read_text(encoding="ascii").strip() == str(os.getpid()):
            pid_file.unlink()


if __name__ == "__main__":
    main()
