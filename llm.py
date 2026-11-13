"""Shared AI provider setup for the ai-chat and ai-actions plugins.

The same file is copied into both plugin folders (plugins never import from
each other). It holds:

- Config: which provider (Anthropic, an OpenAI-compatible endpoint, a local
  Ollama or LM Studio), its base URL, model and API key, stored as provider.json in
  NOOK_DATA_DIR. The key is encrypted with Windows DPAPI (only this Windows
  user can read it back). Settings are shared: loading picks the newest
  provider.json among this plugin and its siblings, so setting a key in one
  plugin sets it for the other.
- settings_view / handle_event: the provider settings page, the same in both.
- stream(): one streamed request, calling on_text with each piece of text.

Network calls: only to the base URL configured here, only when the user sends
something or presses Test.
"""

import base64
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request

import nook

PROVIDERS = [("anthropic", "Anthropic"), ("openai", "OpenAI-compatible"), ("ollama", "Ollama"),
             ("lmstudio", "LM Studio")]
LOCAL = ("ollama", "lmstudio")  # no API key
DEFAULTS = {
    "anthropic": {"base_url": "https://api.anthropic.com", "model": "claude-opus-5-5"},
    "openai": {"base_url": "https://api.openai.com/v1", "model": ""},
    "ollama": {"base_url": "http://localhost:11434", "model": "llama3.2"},
    # An empty model means whichever model LM Studio has loaded.
    "lmstudio": {"base_url": "http://localhost:1234/v1", "model": ""},
}
SIBLINGS = ("ai-chat", "ai-actions")
FILE = "provider.json"
ANTHROPIC_VERSION = "2026-10-21"
# Models that take the server-side refusal fallback and output_config.effort.
FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-sonnet-5-5"}
EFFORT_MODELS = FALLBACK_MODELS | {"claude-haiku-5-5", "claude-fable-5"}
TIMEOUT = 300  # seconds between bytes; thinking can pause the stream for a while


class LLMError(Exception):
    pass


# Key storage -----------------------------------------------------------------

def _dpapi(data, protect):
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    crypt = ctypes.WinDLL("crypt32")
    kernel = ctypes.WinDLL("kernel32")
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    buf = ctypes.create_string_buffer(data, len(data))
    blob_in = Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    blob_out = Blob()
    fn = crypt.CryptProtectData if protect else crypt.CryptUnprotectData
    # 0x1 = CRYPTPROTECT_UI_FORBIDDEN
    if not fn(ctypes.byref(blob_in), None, None, None, None, 0x1, ctypes.byref(blob_out)):
        raise OSError("DPAPI failed")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel.LocalFree(ctypes.cast(blob_out.pbData, ctypes.c_void_p))


def seal(secret):
    if not secret:
        return ""
    if sys.platform == "win32":
        try:
            return "dpapi:" + base64.b64encode(_dpapi(secret.encode("utf-8"), True)).decode()
        except OSError:
            pass
    return "plain:" + secret


def unseal(stored):
    if not stored:
        return ""
    kind, _, body = stored.partition(":")
    if kind == "dpapi":
        try:
            return _dpapi(base64.b64decode(body), False).decode("utf-8")
        except (OSError, ValueError):
            return ""
    return body if kind == "plain" else ""


# Config ----------------------------------------------------------------------

class Config:
    def __init__(self, data_dir):
        self.data_dir = data_dir
        self.path = os.path.join(data_dir, FILE)
        self.data = {}
        self.gen = 0          # bumped to clear the key field after a submit
        self.status = ""      # last Test result, shown on the settings page
        self.reload()

    def _candidates(self):
        parent = os.path.dirname(os.path.abspath(self.data_dir))
        yield self.path
        for sib in SIBLINGS:
            p = os.path.join(parent, sib, FILE)
            if os.path.abspath(p) != os.path.abspath(self.path):
                yield p

    def reload(self):
        """Loads the newest provider.json among this plugin and its siblings."""
        best, best_time = None, -1.0
        for p in self._candidates():
            try:
                t = os.path.getmtime(p)
            except OSError:
                continue
            if t > best_time:
                best, best_time = p, t
        data = {}
        if best:
            try:
                with open(best, encoding="utf-8") as f:
                    data = json.load(f)
            except (OSError, ValueError):
                data = {}
        self.data = data if isinstance(data, dict) else {}

    def save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=1)
        os.replace(tmp, self.path)

    @property
    def provider(self):
        p = self.data.get("provider")
        return p if p in DEFAULTS else "anthropic"

    def section(self, provider=None):
        p = provider or self.provider
        sec = dict(DEFAULTS[p])
        sec.update({k: v for k, v in self.data.get(p, {}).items() if v not in (None, "")})
        return sec

    def get(self, field):
        if field == "key":
            return unseal(self.data.get(self.provider, {}).get("key", ""))
        return self.section().get(field, "")

    def set(self, field, value):
        sec = self.data.setdefault(self.provider, {})
        sec[field] = seal(value) if field == "key" else value
        self.save()

    def ready(self):
        """None when a request can be sent, else what is missing."""
        p = self.provider
        if p == "anthropic" and not self.get("key"):
            return "Add your Anthropic API key in settings"
        if not self.get("model") and p != "lmstudio":
            return "Choose a model in settings"
        return None

    def label(self):
        fallback = "loaded model" if self.provider == "lmstudio" else "no model"
        return f"{dict(PROVIDERS)[self.provider]} · {self.get('model') or fallback}"


# Settings page -----------------------------------------------------------------

def settings_view(cfg):
    p = cfg.provider
    names = [n for _, n in PROVIDERS]
    idx = [k for k, _ in PROVIDERS].index(p)
    key = cfg.get("key")
    fields = []
    if p != "anthropic":
        fields.append(_field("Base URL", nook.text_input(
            f"ai:base_url:{p}", cfg.get("base_url"),
            hint=DEFAULTS[p]["base_url"] + "  (Enter to save)")))
    if p not in LOCAL:
        hint = (f"Saved (…{key[-4:]}). Paste a new key and press Enter to replace it"
                if key else "Paste your API key and press Enter")
        if p == "openai":
            hint += " (leave empty for local servers)" if not key else ""
        fields.append(_field("API key", nook.text_input(f"ai:key:{p}:{cfg.gen}", "", hint=hint)))
    model_hint = {"anthropic": "claude-opus-5-5", "openai": "model name your endpoint serves",
                  "ollama": "e.g. llama3.2, qwen2.5 (ollama list)",
                  "lmstudio": "empty = the model loaded in LM Studio"}[p]
    fields.append(_field("Model", nook.text_input(f"ai:model:{p}", cfg.get("model"),
                                                  hint=model_hint + "  (Enter to save)")))
    status = cfg.status or ("Key and settings are stored only on this PC (key encrypted "
                            "for your Windows user). Shared with the other AI plugin.")
    return nook.column([
        nook.chips("ai:provider", names, selected=idx),
        *fields,
        nook.row([
            nook.text(status, size="small", color="dim", wrap=True),
            nook.spacer(),
            nook.button("ai:test", label="Test", icon="plugs-connected"),
        ], align="center"),
    ], gap=8, scroll=True)


def _field(title, control):
    return nook.column([nook.text(title, size="small", color="dim"), control], gap=2)


def handle_event(cfg, ev, post):
    """Handles a settings control. post(message) hands results from a
    background thread to the plugin's main loop as {"type": "x-ai-status"}.
    Returns True when the event belonged to the settings page."""
    eid = ev.get("id", "")
    if not eid.startswith("ai:"):
        return False
    parts = eid.split(":")
    if eid == "ai:provider" and ev.get("kind") == "change":
        i = (ev.get("value") or {}).get("index", 0)
        cfg.data["provider"] = PROVIDERS[max(0, min(i, len(PROVIDERS) - 1))][0]
        cfg.status = ""
        cfg.save()
    elif parts[1] in ("base_url", "model") and ev.get("kind") == "submit":
        cfg.set(parts[1], str(ev.get("value") or "").strip())
        cfg.status = "Saved."
    elif parts[1] == "key" and ev.get("kind") == "submit":
        cfg.set("key", str(ev.get("value") or "").strip())
        cfg.gen += 1
        cfg.status = "Key saved." if cfg.get("key") else "Key removed."
    elif eid == "ai:test":
        cfg.status = "Testing…"
        threading.Thread(target=_test, args=(cfg, post), daemon=True).start()
    return True


def _test(cfg, post):
    try:
        missing = cfg.ready()
        if missing:
            raise LLMError(missing)
        start = time.monotonic()
        out = stream(cfg, "Reply with the single word: ok", [{"role": "user", "content": "ping"}],
                     lambda t: None, threading.Event(), effort="low", max_tokens=200)
        msg = f"Works: {cfg.label()} answered “{out.strip()[:30]}” in {time.monotonic() - start:.1f} s."
    except LLMError as e:
        msg = f"Failed: {e}"
    post({"type": "x-ai-status", "text": msg})


# Requests -------------------------------------------------------------------------

def stream(cfg, system, messages, on_text, cancel, effort=None, max_tokens=16000):
    """Sends one chat request and streams the answer. messages are
    [{"role": "user"|"assistant", "content": str}]. Returns the full text;
    raises LLMError. Stops early when cancel (a threading.Event) is set."""
    cfg.reload()
    p = cfg.provider
    if p == "anthropic":
        url, headers, body, parse = _anthropic(cfg, system, messages, effort, max_tokens)
    elif p == "openai":
        url, headers, body, parse = _openai(cfg, system, messages)
    elif p == "lmstudio":
        url, headers, body, parse = _openai(cfg, system, messages, _lmstudio_model(cfg))
    else:
        url, headers, body, parse = _ollama(cfg, system, messages)
    headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"),
                                 headers=headers, method="POST")
    parts = []
    try:
        resp = urllib.request.urlopen(req, timeout=TIMEOUT)
    except urllib.error.HTTPError as e:
        raise LLMError(_http_error(e)) from None
    except (urllib.error.URLError, OSError) as e:
        reason = getattr(e, "reason", e)
        hint = " Is LM Studio's local server running (Developer tab)?" if p == "lmstudio" else ""
        raise LLMError(f"Can't reach {url}: {reason}.{hint}") from None
    try:
        for raw in resp:
            if cancel.is_set():
                break
            piece = parse(raw.decode("utf-8", "replace").strip())
            if piece is _DONE:
                break
            if piece:
                parts.append(piece)
                on_text(piece)
    except (OSError, ValueError) as e:
        if not cancel.is_set():
            raise LLMError(f"Connection dropped: {e}") from None
    finally:
        resp.close()
    return "".join(parts)


_DONE = object()


def _http_error(e):
    try:
        data = json.loads(e.read().decode("utf-8", "replace"))
        err = data.get("error", data)
        msg = err.get("message") if isinstance(err, dict) else str(err)
    except (ValueError, OSError, AttributeError):
        msg = ""
    hint = {401: "check the API key", 403: "the key can't use this model",
            404: "check the base URL and model", 429: "rate limited, try again shortly"}.get(e.code, "")
    return f"HTTP {e.code}" + (f": {msg}" if msg else "") + (f" ({hint})" if hint else "")


def _anthropic(cfg, system, messages, effort, max_tokens):
    model = cfg.get("model")
    headers = {"x-api-key": cfg.get("key"), "anthropic-version": ANTHROPIC_VERSION}
    body = {"model": model, "max_tokens": max_tokens, "stream": True,
            "system": system, "messages": messages}
    if effort and model in EFFORT_MODELS:
        body["output_config"] = {"effort": effort}
    if model in FALLBACK_MODELS:
        # If a safety classifier declines, the API re-runs the request on a
        # fallback model inside the same call instead of just stopping.
        body["fallbacks"] = "default"
        headers["anthropic-beta"] = "server-side-fallback-2026-07-01"
    base = cfg.get("base_url").rstrip("/")

    def parse(line):
        if not line.startswith("data:"):
            return None
        ev = json.loads(line[5:])
        t = ev.get("type")
        if t == "content_block_delta" and ev.get("delta", {}).get("type") == "text_delta":
            return ev["delta"].get("text", "")
        if t == "message_delta" and ev.get("delta", {}).get("stop_reason") == "refusal":
            raise LLMError("The model declined to answer this request.")
        if t == "error":
            raise LLMError(ev.get("error", {}).get("message", "API error"))
        if t == "message_stop":
            return _DONE
        return None
    return base + "/v1/messages", headers, body, parse


def _lmstudio_model(cfg):
    """The configured model, else the first one LM Studio's server lists."""
    if cfg.get("model"):
        return cfg.get("model")
    url = cfg.get("base_url").rstrip("/") + "/models"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            models = json.load(resp).get("data") or []
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise LLMError(f"Can't reach LM Studio at {url}: {getattr(e, 'reason', e)}. "
                       "Start its local server (Developer tab).") from None
    ids = [m.get("id") for m in models if m.get("id") and "embed" not in m.get("id")]
    if not ids:
        raise LLMError("LM Studio has no model loaded. Load one, or set a model name in settings.")
    return ids[0]


def _openai(cfg, system, messages, model=None):
    headers = {}
    if cfg.get("key"):
        headers["Authorization"] = "Bearer " + cfg.get("key")
    body = {"model": model or cfg.get("model"), "stream": True,
            "messages": [{"role": "system", "content": system}] + messages}

    def parse(line):
        if not line.startswith("data:"):
            return None
        data = line[5:].strip()
        if data == "[DONE]":
            return _DONE
        ev = json.loads(data)
        if "error" in ev:
            err = ev["error"]
            raise LLMError(err.get("message", str(err)) if isinstance(err, dict) else str(err))
        choices = ev.get("choices") or [{}]
        return (choices[0].get("delta") or {}).get("content") or None
    return cfg.get("base_url").rstrip("/") + "/chat/completions", headers, body, parse


def _ollama(cfg, system, messages):
    body = {"model": cfg.get("model"), "stream": True,
            "messages": [{"role": "system", "content": system}] + messages}

    def parse(line):
        if not line:
            return None
        ev = json.loads(line)
        if ev.get("error"):
            raise LLMError(str(ev["error"]))
        piece = (ev.get("message") or {}).get("content") or None
        return _DONE if ev.get("done") and not piece else piece
    return cfg.get("base_url").rstrip("/") + "/api/chat", {}, body, parse
