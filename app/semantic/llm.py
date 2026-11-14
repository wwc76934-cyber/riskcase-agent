"""Small, budgeted, cached chat client for the semantic fraud module.

* Streaming transport (same SSE folding as the Gate 4 client).
* A SQLite ledger with a hard cap. A call is refused before it is sent if recorded spend plus the
  worst-case cost of this call would pass the cap. A call that times out is recorded at its
  worst-case cost, because the provider may have billed it.
* A response cache keyed by model, prompts and settings, so a rerun does not pay twice.
* Safe to use from several threads.

Rates are estimates in CNY per million tokens. Provider billing is authoritative.
"""
from contextlib import closing
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sqlite3
import threading
import time

import httpx

from app.investigation.v3.gate4_stream import aggregate_sse
from app.llm.credentials import load_key

ENDPOINT = "https://api.siliconflow.cn/v1/chat/completions"
RATES = {  # (input, output) CNY per million tokens. DeepSeek and GLM rates are conservative guesses.
    "Qwen/Qwen3.8-27B": (Decimal("3"), Decimal("12")),
    "deepseek-ai/DeepSeek-V4-Flash": (Decimal("4"), Decimal("16")),
    "Qwen/Qwen2.5-7B-Instruct": (Decimal("0.5"), Decimal("0.5")),  # upper estimate; the provider lists it lower
}
SCHEMA = """
CREATE TABLE IF NOT EXISTS calls(id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, model TEXT, purpose TEXT,
  prompt_tokens INTEGER, completion_tokens INTEGER, cny TEXT, status TEXT, elapsed REAL, cache_key TEXT);
CREATE TABLE IF NOT EXISTS cache(cache_key TEXT PRIMARY KEY, response TEXT, prompt_tokens INTEGER, completion_tokens INTEGER);
"""


class BudgetExceeded(RuntimeError):
    pass


class LLMUnavailable(RuntimeError):
    pass


class HTTPRejected(Exception):
    """The provider answered with a non-200 status. The request was refused, so it is not billed."""
    def __init__(self, status):
        super().__init__(f"http_{status}")
        self.status = status


def estimate_tokens(text):
    return int(len(text) / 1.2) + 50  # Chinese text runs close to one token per character; deliberately high


class Budget:
    def __init__(self, path, cap_cny):
        self.path, self.cap = Path(path), Decimal(str(cap_cny))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        with closing(sqlite3.connect(self.path)) as db, db:
            db.executescript(SCHEMA)

    def spent(self):
        with closing(sqlite3.connect(self.path)) as db:
            rows = db.execute("SELECT cny FROM calls").fetchall()
        return sum((Decimal(r[0]) for r in rows), Decimal(0))

    def calls(self, purpose=None):
        with closing(sqlite3.connect(self.path)) as db:
            q = "SELECT COUNT(*), COALESCE(SUM(CAST(cny AS REAL)),0) FROM calls" + (" WHERE purpose=?" if purpose else "")
            return db.execute(q, (purpose,) if purpose else ()).fetchone()

    def record(self, **row):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("INSERT INTO calls(ts,model,purpose,prompt_tokens,completion_tokens,cny,status,elapsed,cache_key) "
                       "VALUES (?,?,?,?,?,?,?,?,?)", (time.time(), row["model"], row["purpose"], row["pt"], row["ct"],
                                                      str(row["cny"]), row["status"], row["elapsed"], row["cache_key"]))

    def cached(self, key):
        with closing(sqlite3.connect(self.path)) as db:
            return db.execute("SELECT response,prompt_tokens,completion_tokens FROM cache WHERE cache_key=?", (key,)).fetchone()

    def store(self, key, response, pt, ct):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("INSERT OR REPLACE INTO cache VALUES (?,?,?,?)", (key, response, pt, ct))


def cost(model, pt, ct):
    rin, rout = RATES[model]
    return (pt * rin + ct * rout) / Decimal(1_000_000)


class LLM:
    def __init__(self, budget, *, key=None, read_timeout=180.0, total_timeout=420.0, attempts=2):
        self.budget, self.key = budget, key or load_key()
        if not self.key:
            raise LLMUnavailable("no local credential")
        self.read_timeout, self.total_timeout, self.attempts = read_timeout, total_timeout, attempts
        self.http = httpx.Client(transport=httpx.HTTPTransport(retries=0), follow_redirects=False, trust_env=False)

    def close(self):
        self.http.close()

    def chat(self, model, system, user, *, purpose, max_tokens=1200, temperature=0.0, json_mode=True):
        """Return ``(text, meta)``. ``meta`` has cached, cny, prompt_tokens, completion_tokens, elapsed."""
        key = hashlib.sha256(json.dumps([model, system, user, max_tokens, temperature, json_mode],
                                        ensure_ascii=False).encode()).hexdigest()
        hit = self.budget.cached(key)
        if hit:
            return hit[0], dict(cached=True, cny=Decimal(0), prompt_tokens=hit[1], completion_tokens=hit[2], elapsed=0.0)
        worst = cost(model, estimate_tokens(system + user), max_tokens)
        with self.budget.lock:
            if self.budget.spent() + worst > self.budget.cap:
                raise BudgetExceeded(f"cap {self.budget.cap} CNY would be passed")
            self.budget.record(model=model, purpose=purpose, pt=0, ct=0, cny=worst, status="reserved", elapsed=0, cache_key=key)
            reservation = self._last_id()
        payload = dict(model=model, stream=True, stream_options={"include_usage": True}, temperature=temperature,
                       max_tokens=max_tokens,
                       messages=[dict(role="system", content=system), dict(role="user", content=user)])
        if "Qwen2.5" not in model:
            payload["enable_thinking"] = False
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        last_error, network_failures, rejections = None, 0, 0
        while network_failures < self.attempts and rejections < 8:
            begin = time.monotonic()
            try:
                text, usage = self._once(payload, begin)
            except HTTPRejected as exc:
                rejections += 1
                last_error = str(exc)
                if exc.status not in (408, 429, 500, 502, 503, 504):
                    self._settle(reservation, 0, 0, Decimal(0), "rejected:" + last_error, 0.0)
                    raise LLMUnavailable(last_error)
                time.sleep(min(60, 4 * 2 ** rejections))
                continue
            except (httpx.HTTPError, TimeoutError, ValueError, RuntimeError) as exc:
                network_failures += 1
                last_error = f"{type(exc).__name__}"
                time.sleep(2 * network_failures)
                continue
            pt, ct = usage
            actual = cost(model, pt, ct)
            self._settle(reservation, pt, ct, actual, "ok", time.monotonic() - begin)
            self.budget.store(key, text, pt, ct)
            return text, dict(cached=False, cny=actual, prompt_tokens=pt, completion_tokens=ct, elapsed=time.monotonic() - begin)
        if network_failures < self.attempts:  # only provider refusals: nothing was billed
            self._settle(reservation, 0, 0, Decimal(0), 'rejected:' + str(last_error), 0.0)
            raise LLMUnavailable(last_error or 'failed')
        # leave the worst-case reservation in place: the provider may have billed a call we never saw finish
        self._settle(reservation, 0, 0, worst, 'unknown:' + str(last_error), 0.0)
        raise LLMUnavailable(last_error or "failed")

    def _last_id(self):
        with closing(sqlite3.connect(self.budget.path)) as db:
            return db.execute("SELECT MAX(id) FROM calls").fetchone()[0]

    def _settle(self, row_id, pt, ct, actual, status, elapsed):
        with closing(sqlite3.connect(self.budget.path)) as db, db:
            db.execute("UPDATE calls SET prompt_tokens=?,completion_tokens=?,cny=?,status=?,elapsed=? WHERE id=?",
                       (pt, ct, str(actual), status, elapsed, row_id))

    def _once(self, payload, begin):
        headers = {"Authorization": "Bearer " + self.key, "Content-Type": "application/json"}
        timeout = httpx.Timeout(self.read_timeout, connect=10.0)
        body = bytearray()
        with self.http.stream("POST", ENDPOINT, headers=headers, content=json.dumps(payload, ensure_ascii=False).encode(),
                              timeout=timeout) as response:
            if response.status_code != 200:
                raise HTTPRejected(response.status_code)
            for chunk in response.iter_bytes():
                body.extend(chunk)
                if len(body) > 8 * 1024 * 1024 or time.monotonic() - begin > self.total_timeout:
                    raise TimeoutError("total deadline")
        data = aggregate_sse(bytes(body))
        text = data["choices"][0]["message"]["content"]
        usage = data.get("usage") or {}
        pt = int(usage.get("prompt_tokens") or estimate_tokens(json.dumps(payload["messages"], ensure_ascii=False)))
        ct = int(usage.get("completion_tokens") or estimate_tokens(text))
        return text, (pt, ct)


def parse_json(text):
    """Parse a model reply that should be one JSON object. Returns None when it is not."""
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text[text.find("{"):] if "{" in text else text
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        value = json.loads(text[start:end + 1])
    except ValueError:
        return None
    return value if isinstance(value, dict) else None
