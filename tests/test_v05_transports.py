import json
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_TOOLS = {
    "tools": [
        {
            "name": "sse_echo",
            "description": "Echo",
            "inputSchema": {"type": "object", "properties": {}},
        }
    ]
}


class _SseHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(b"event: endpoint\ndata: /messages\n\n")
        self.wfile.flush()
        self.server.sse_writer = self.wfile
        self.server.sse_ready.set()
        while not self.server.stop_event.is_set():
            time.sleep(0.02)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        message = json.loads(self.rfile.read(length))
        method = message.get("method")
        if method == "initialize":
            result = {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "sse", "version": "1"},
            }
        elif method == "tools/list":
            result = _TOOLS
        else:
            result = {}
        body = json.dumps({"jsonrpc": "2.0", "id": message.get("id"), "result": result})
        writer = self.server.sse_writer
        if writer is not None:
            with self.server.sse_lock:
                writer.write(f"event: message\ndata: {body}\n\n".encode())
                writer.flush()
        self.send_response(202)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args):
        return


class SseTransportTests(unittest.TestCase):
    def test_legacy_sse(self):
        from lubanlock.integrations.mcp_client import discover_server

        server = ThreadingHTTPServer(("127.0.0.1", 0), _SseHandler)
        server.sse_writer = None
        server.sse_ready = threading.Event()
        server.stop_event = threading.Event()
        server.sse_lock = threading.Lock()
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_port}/sse"
            report = discover_server("sse", {"type": "sse", "url": url}, timeout=5)
            self.assertEqual([tool.name for tool in report.tools], ["sse_echo"])
        finally:
            server.stop_event.set()
            server.shutdown()
            server.server_close()


class WebSocketTransportTests(unittest.TestCase):
    def test_websocket(self):
        try:
            from websockets.sync.server import serve
        except ImportError:
            self.skipTest("websockets is not installed")
        from lubanlock.integrations.mcp_client import discover_server

        def handler(websocket):
            for raw in websocket:
                message = json.loads(raw)
                method = message.get("method")
                if method == "initialize":
                    result = {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "ws", "version": "1"},
                    }
                elif method == "tools/list":
                    result = _TOOLS
                else:
                    result = {}
                websocket.send(
                    json.dumps(
                        {"jsonrpc": "2.0", "id": message.get("id"), "result": result}
                    )
                )

        server = serve(handler, "127.0.0.1", 0)
        port = server.socket.getsockname()[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"ws://127.0.0.1:{port}"
            report = discover_server("ws", {"type": "ws", "url": url}, timeout=5)
            self.assertEqual([tool.name for tool in report.tools], ["sse_echo"])
        finally:
            server.shutdown()


if __name__ == "__main__":
    unittest.main()
