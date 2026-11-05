"""A stand-in OpenAI-compatible server for tests: it searches, reads, then answers citing what it read (plus one
made-up id the app must drop). Streams like llama.cpp does, with reasoning_content and tool_calls deltas."""
import http.server
import json
import re
import threading


def _chunks(delta_list, finish):
    for d in delta_list:
        yield {"choices": [{"index": 0, "delta": d, "finish_reason": None}]}
    yield {"choices": [{"index": 0, "delta": {}, "finish_reason": finish}]}
    yield {"choices": [], "usage": {"completion_tokens": 42}}


def respond(body):
    msgs = body["messages"]
    tools = body.get("tools")
    last_user = [m for m in msgs if m["role"] == "user"][-1]["content"]
    if isinstance(last_user, list):  # text and an image: describe the drawing, or correct a description of it
        has_image = any(part.get("type") == "image_url" for part in last_user)
        if "You sort tables" in msgs[0]["content"]:  # a table of ticks needs a description, plain text doesn't
            text = " ".join(part.get("text", "") for part in last_user)
            return _chunks([{"content": "yes" if "\uf0fc" in text else "no"}], "stop")
        if "You correct descriptions" in msgs[0]["content"]:  # the drawing says 5m
            text = " ".join(part.get("text", "") for part in last_user).split("Description to correct:\n", 1)[1]
            return _chunks([{"content": text.replace("4m", "5m")}], "stop")
        return _chunks([{"content": "A drawing of a stair in section." if has_image else "No image."}], "stop")
    if not tools:
        if "Reply with just: OK" in last_user:
            return _chunks([{"content": "OK"}], "stop")
        ids = re.findall(r"\[([rf]\d+)\]", last_user)
        return _chunks([{"content": f"From the excerpts: rise 220 mm [{ids[0] if ids else 'r1'}]."}], "stop")
    if "from memory" in last_user and msgs[-1]["role"] == "user":  # answers without looking up, until the app asks
        return _chunks([{"content": "A higher-risk building has 18 storeys or more."}], "stop")
    since_user = []
    for m in reversed(msgs):
        if m["role"] == "user":
            break
        since_user.append(m)
    tools_this_turn = [m for m in since_user if m["role"] == "tool"]
    if not tools_this_turn:
        return _chunks([{"reasoning_content": "I should search. "},
                        {"tool_calls": [{"index": 0, "id": "c1", "type": "function",
                                         "function": {"name": "search_library", "arguments": ""}}]},
                        {"tool_calls": [{"index": 0, "function": {"arguments": json.dumps(
                            {"query": "Part K private stair rise"})}}]}], "tool_calls")
    if len(tools_this_turn) == 1:
        ids = re.findall(r"\[([rf]\d+)\]", tools_this_turn[0]["content"])
        return _chunks([{"content": "Let me read that."},
                        {"tool_calls": [{"index": 0, "id": "c2", "type": "function",
                                         "function": {"name": "read_passage",
                                                      "arguments": json.dumps({"id": ids[0]})}}]}], "tool_calls")
    ids = re.findall(r"\[([rf]\d+)\]", tools_this_turn[0]["content"])
    return _chunks([{"content": "<think>checking</think>The maximum rise for a private stair is **220 mm** "},
                    {"content": f"(ADK para 1.2) [{ids[0]}]. Headroom is 2.0 m [r99999]."}], "stop")


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path.endswith("/models"):
            data = json.dumps({"data": [{"id": "fake-model"}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for c in respond(body):
            self.wfile.write(b"data: " + json.dumps(c).encode() + b"\n\n")
        self.wfile.write(b"data: [DONE]\n\n")


def start():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}/v1"
