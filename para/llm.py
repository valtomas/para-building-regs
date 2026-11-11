"""A small client for any OpenAI-compatible chat server (Ollama, LM Studio, llama.cpp, Lemonade, vLLM, Jan, a cloud
API...). Standard library only. Streams /chat/completions and turns the deltas into plain events."""
import ipaddress
import json
import re
import socket
import ssl
import urllib.error
import urllib.parse
import urllib.request

# Ports the common local servers listen on, tried by "Find engines on this computer".
KNOWN_SERVERS = [
    ("Ollama", "http://127.0.0.1:11434/v1"),
    ("LM Studio", "http://127.0.0.1:1234/v1"),
    ("Lemonade", "http://127.0.0.1:8000/api/v1"),
    ("Lemonade", "http://127.0.0.1:13305/api/v1"),
    ("llama.cpp server", "http://127.0.0.1:8080/v1"),
    ("Jan", "http://127.0.0.1:1337/v1"),
    ("LocalAI", "http://127.0.0.1:8081/v1"),
    ("vLLM", "http://127.0.0.1:8001/v1"),
]

# Hosted services offered in Settings. Each speaks the same /chat/completions API, so choosing one only fills in the
# address: there's no code for any one of them. "where" is said beside the name because it's what decides whose
# data-protection law covers the questions and file passages sent there.
HOSTED = [
    {"name": "Anthropic", "base_url": "https://api.anthropic.com/v1", "where": "Claude models. Based in the US.",
     "keys": "https://platform.claude.com/settings/keys", "example": "claude-sonnet-5-5"},
    {"name": "Mistral", "base_url": "https://api.mistral.ai/v1", "where": "Mistral models. Based in France, under "
     "EU data-protection law.", "keys": "https://console.mistral.ai/api-keys", "example": "mistral-large-latest"},
    {"name": "OpenAI", "base_url": "https://api.openai.com/v1", "where": "GPT models. Based in the US.",
     "keys": "https://platform.openai.com/api-keys", "example": "gpt-5-mini"},
    {"name": "OpenRouter", "base_url": "https://openrouter.ai/api/v1", "where": "One key for many companies' models; "
     "each question goes to the company that runs the model you choose.", "keys": "https://openrouter.ai/settings/keys",
     "example": "mistralai/mistral-large"},
]


class EngineError(Exception):
    pass


def _context():
    try:
        import truststore  # the system's certificates, for company proxies
        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except Exception:  # noqa: BLE001
        return ssl.create_default_context()


def same_host(a, b):
    """True when two addresses point at the same server, so a key saved for one may be sent to the other."""
    pa, pb = urllib.parse.urlparse(a or ""), urllib.parse.urlparse(b or "")
    return bool(pa.hostname) and (pa.scheme, pa.hostname, pa.port) == (pb.scheme, pb.hostname, pb.port)


def is_local(base_url):
    """True for this computer or the local network. The address is read as an address, not matched by how it starts:
    "10.example.com" is a name anyone can register, and calling it private would be untrue."""
    host = (urllib.parse.urlparse(base_url).hostname or "").lower()
    if host == "localhost" or host.endswith(".local"):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:  # any other name: somewhere else, as far as the app can tell
        return False
    return ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_unspecified


class Client:
    def __init__(self, base_url, api_key="", model=""):
        self.base = base_url.rstrip("/")
        self.key, self.model = api_key, model
        # what this server turned out not to take, so a turn's later requests aren't each refused first
        self.length_field, self.bare = "max_tokens", False

    def _request(self, path, body=None, timeout=30):
        headers = {"Content-Type": "application/json"}
        if self.key:
            headers["Authorization"] = f"Bearer {self.key}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, headers=headers)
        try:
            return urllib.request.urlopen(req, timeout=timeout, context=_context() if self.base.startswith("https")
                                          else None)
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:600]
            raise EngineError(f"The engine answered {e.code}: {detail}") from None
        except (urllib.error.URLError, socket.timeout, ConnectionError) as e:
            raise EngineError(f"Can't reach the engine at {self.base} ({getattr(e, 'reason', e)})") from None

    def models(self, timeout=5):
        with self._request("/models", timeout=timeout) as r:
            data = json.load(r)
        items = data.get("data") or data.get("models") or []
        ids = {m.get("id") or m.get("name") for m in items if isinstance(m, dict)} - {None}
        # embedding, reranking, speech, image and moderation models can't chat: leave them out of the choice
        return sorted(i for i in ids if not re.search(
            r"embed|rerank|moderation|whisper|tts|transcribe|dall-e|image|audio|realtime|ocr|babbage|davinci", i, re.I))

    def stream(self, messages, tools=None, max_tokens=None, reasoning=None, extra=None):
        """Yields {"type": "content"|"reasoning", "text"}, {"type": "tool_calls", "calls": [...]}, then
        {"type": "end", "finish": reason, "usage": {...}}."""
        body = {"model": self.model, "messages": messages, "stream": True,
                "stream_options": {"include_usage": True}}
        if tools:
            body["tools"] = tools
        if max_tokens and max_tokens > 0:
            body[self.length_field] = max_tokens
        if reasoning == "off":
            # Understood by llama.cpp/Qwen templates and ignored by servers that don't know it.
            body["chat_template_kwargs"] = {"enable_thinking": False}
            body["reasoning_effort"] = "low"
        elif reasoning == "on":
            body["chat_template_kwargs"] = {"enable_thinking": True}
        if extra:
            body.update(extra)
        optional = ("chat_template_kwargs", "reasoning_effort", "stream_options")
        if self.bare:
            for k in optional:
                body.pop(k, None)
        while True:
            try:
                resp = self._request("/chat/completions", body, timeout=900)
                break
            except EngineError as e:
                if not re.search(r"\b(400|422)\b", str(e)):
                    raise
                if "max_tokens" in body and "max_tokens" in str(e):
                    # Newer services refuse max_tokens by name and want the same limit as max_completion_tokens.
                    self.length_field = "max_completion_tokens"
                    body[self.length_field] = body.pop("max_tokens")
                elif any(k in body for k in optional):
                    # Some servers reject fields they don't know: try again with the bare request.
                    self.bare = True
                    for k in optional:
                        body.pop(k, None)
                else:
                    raise
        calls, finish, usage = {}, None, {}
        think = _ThinkSplitter()
        with resp:
            for raw in resp:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                try:
                    chunk = json.loads(payload)
                except ValueError:
                    continue
                if chunk.get("error"):
                    raise EngineError(str(chunk["error"].get("message") if isinstance(chunk["error"], dict)
                                          else chunk["error"]))
                usage = chunk.get("usage") or usage
                for choice in chunk.get("choices") or []:
                    delta = choice.get("delta") or {}
                    r = delta.get("reasoning_content") or delta.get("reasoning") or delta.get("thinking")
                    if r:
                        yield {"type": "reasoning", "text": r}
                    if delta.get("content"):
                        for kind, text in think.feed(delta["content"]):
                            yield {"type": kind, "text": text}
                    for tc in delta.get("tool_calls") or []:
                        slot = calls.setdefault(tc.get("index", len(calls)),
                                                {"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                        if tc.get("id"):
                            slot["id"] = tc["id"]
                        fn = tc.get("function") or {}
                        if fn.get("name"):
                            slot["function"]["name"] += fn["name"]
                        if fn.get("arguments"):
                            a = fn["arguments"]
                            slot["function"]["arguments"] += a if isinstance(a, str) else json.dumps(a)
                    if choice.get("finish_reason"):
                        finish = choice["finish_reason"]
                if not chunk.get("choices"):
                    yield {"type": "tick"}
        for kind, text in think.flush():
            yield {"type": kind, "text": text}
        if calls:
            out = []
            for i, c in sorted(calls.items()):
                c["id"] = c["id"] or f"call_{i}"
                out.append(c)
            yield {"type": "tool_calls", "calls": out}
        yield {"type": "end", "finish": finish, "usage": usage}


class _ThinkSplitter:
    """Servers that leave <think>...</think> in the content: split it off as reasoning."""
    OPEN, CLOSE = "<think>", "</think>"

    def __init__(self):
        self.buf, self.inside, self.started = "", False, False

    def feed(self, text):
        self.buf += text
        out = []
        while self.buf:
            if self.inside:
                i = self.buf.find(self.CLOSE)
                if i < 0:
                    keep = _partial(self.buf, self.CLOSE)
                    if len(self.buf) > keep:
                        out.append(("reasoning", self.buf[:len(self.buf) - keep]))
                    self.buf = self.buf[len(self.buf) - keep:]
                    break
                if i:
                    out.append(("reasoning", self.buf[:i]))
                self.buf, self.inside = self.buf[i + len(self.CLOSE):].lstrip("\n"), False
                continue
            if not self.started:
                stripped = self.buf.lstrip()
                if stripped.startswith(self.OPEN):
                    self.buf, self.inside, self.started = stripped[len(self.OPEN):], True, True
                    continue
                if self.OPEN.startswith(stripped):  # could still become <think>
                    break
                self.started = True
            out.append(("content", self.buf))
            self.buf = ""
        return out

    def flush(self):
        if not self.buf:
            return []
        out = [("reasoning" if self.inside else "content", self.buf)]
        self.buf = ""
        return out


def _partial(text, tag):
    """How many characters at the end of text could be the start of tag."""
    for n in range(min(len(tag) - 1, len(text)), 0, -1):
        if tag.startswith(text[-n:]):
            return n
    return 0


def probe_known():
    """Engines answering on this computer's usual ports: [{"name", "base_url", "models"}]."""
    import concurrent.futures as cf

    def check(item):
        name, url = item
        try:
            models = Client(url).models(timeout=1.5)
        except Exception:  # noqa: BLE001
            return None
        # nothing to chat with, e.g. the embedding backend Lemonade runs on 8001
        return {"name": name, "base_url": url, "models": models} if models else None

    with cf.ThreadPoolExecutor(len(KNOWN_SERVERS)) as pool:
        found = [r for r in pool.map(check, KNOWN_SERVERS) if r]
    seen, out = set(), []
    for f in found:
        if f["base_url"] not in seen:
            seen.add(f["base_url"])
            out.append(f)
    return out
