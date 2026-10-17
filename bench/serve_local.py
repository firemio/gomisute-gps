#!/usr/bin/env python3
"""計測用: mcp_server と同じ処理を、データを読み込んだまま localhost の HTTP で受ける (MCP サーバーが起動済みの状態と同じにする)

  python bench/serve_local.py [port]        # 既定 8765
  python bench/gomisute_cli.py address "金沢市笠舞2丁目29番5号" --date 2026-10-20
"""
import json, os, sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import mcp_server

class H(BaseHTTPRequestHandler):
    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])).decode("utf-8"))
        try: body = {"ok": True, "result": mcp_server.call(req["name"], req.get("arguments") or {})}
        except Exception as e: body = {"ok": False, "error": str(e)}
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(200); self.send_header("Content-Type", "application/json; charset=utf-8"); self.send_header("Content-Length", str(len(data))); self.end_headers()
        self.wfile.write(data)
    def log_message(self, *a): pass

if __name__ == "__main__":
    mcp_server._ready.wait(180)
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    print(f"ready on 127.0.0.1:{port}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
