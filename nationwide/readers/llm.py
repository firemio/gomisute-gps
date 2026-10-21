"""読み取りに使う LLM の呼び出し (OpenAI 互換の Chat Completions)。モデルと接続先は環境変数で切り替える

  GOMISUTE_LLM_BASE_URL   既定 https://api.deepseek.com
  GOMISUTE_LLM_MODEL      既定 deepseek-flash (DeepSeek-V4.1-Flash。画像も読める)
  GOMISUTE_LLM_API_KEY    API キー (リポジトリには書かない)

画像は data URL (base64) で渡す。答えは JSON だけを返させ、使ったトークンを記録する
"""
import base64, json, os, time, urllib.error, urllib.request

BASE = os.environ.get("GOMISUTE_LLM_BASE_URL", "https://api.deepseek.com").rstrip("/")
MODEL = os.environ.get("GOMISUTE_LLM_MODEL", "deepseek-flash")

def _key():
    k = os.environ.get("GOMISUTE_LLM_API_KEY")
    if not k: raise RuntimeError("環境変数 GOMISUTE_LLM_API_KEY に API キーを設定してください")
    return k

def image_part(png_bytes):
    return {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(png_bytes).decode("ascii")}}

def cache_key(prompt_version, system, text, model=None):
    """問いかけの版・モデル・system・入力 → 答えのキャッシュの鍵 (sha256)"""
    import hashlib
    h = hashlib.sha256()
    for x in (prompt_version, model or MODEL, system, text):
        h.update(x.encode("utf-8"))
        h.update(b"\0")
    return h.hexdigest()


def ask_cached(prompt_version, system, text, cache_dir, model=None, max_tokens=32000, offline=False):
    """文字だけの問いかけ → (答え, 記録)。答えは cache_dir/<鍵の頭 2 字>/<鍵>.json に覚え、同じものは聞き直さない。
    offline (キーを使わない) でキャッシュに無ければ (None, {"cached": False, "missing": True})"""
    import pathlib
    key = cache_key(prompt_version, system, text, model)
    p = pathlib.Path(cache_dir) / key[:2] / f"{key}.json"
    if p.exists():
        rec = json.loads(p.read_text("utf-8"))
        return rec["answer"], {**rec.get("usage", {}), "cached": True, "key": key}
    if offline:
        return None, {"cached": False, "missing": True, "key": key}
    ans, usage = ask_json(system, [{"type": "text", "text": text}], model=model, max_tokens=max_tokens)
    p.parent.mkdir(parents=True, exist_ok=True)
    rec = {"prompt": prompt_version, "model": usage.get("model") or model or MODEL, "key": key, "usage": usage, "answer": ans,
           "at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    tmp = p.with_suffix(".part")
    tmp.write_text(json.dumps(rec, ensure_ascii=False, indent=1), "utf-8")
    os.replace(tmp, p)
    return ans, {**usage, "cached": False, "key": key}


def ask_cached_parts(prompt_version, system, text, images, cache_dir, model=None, max_tokens=32000, offline=False):
    """文字と画像 (PNG のバイト列のリスト) の問いかけ → (答え, 記録)。鍵には画像の sha256 も入れる。ほかは ask_cached と同じ"""
    import hashlib, pathlib
    key = cache_key(prompt_version, system, text + "\0" + "\0".join(hashlib.sha256(b).hexdigest() for b in images), model)
    p = pathlib.Path(cache_dir) / key[:2] / f"{key}.json"
    if p.exists():
        rec = json.loads(p.read_text("utf-8"))
        return rec["answer"], {**rec.get("usage", {}), "cached": True, "key": key}
    if offline:
        return None, {"cached": False, "missing": True, "key": key}
    parts = [{"type": "text", "text": text}] + [image_part(b) for b in images]
    ans, usage = ask_json(system, parts, model=model, max_tokens=max_tokens)
    p.parent.mkdir(parents=True, exist_ok=True)
    rec = {"prompt": prompt_version, "model": usage.get("model") or model or MODEL, "key": key, "usage": usage, "answer": ans,
           "images": [hashlib.sha256(b).hexdigest() for b in images], "at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    tmp = p.with_suffix(".part")
    tmp.write_text(json.dumps(rec, ensure_ascii=False, indent=1), "utf-8")
    os.replace(tmp, p)
    return ans, {**usage, "cached": False, "key": key}


def ask_json(system, parts, model=None, max_tokens=32000, retries=4, effort=None):
    """system と、文字・画像の部品 → (JSON の答え, 使ったトークンなど)。JSON として読めなければ例外"""
    body = {"model": model or MODEL, "max_tokens": max_tokens, "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": parts}]}
    if effort: body["reasoning_effort"] = effort
    data = json.dumps(body).encode("utf-8")
    for i in range(retries):
        req = urllib.request.Request(BASE + "/chat/completions", data=data, method="POST",
                                     headers={"Authorization": "Bearer " + _key(), "Content-Type": "application/json"})
        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=600) as r: res = json.load(r)
            ch = res["choices"][0]
            text = ch["message"].get("content") or ""
            if not text.strip() and ch.get("finish_reason") == "length":
                raise RuntimeError(f"答えが上限 max_tokens={max_tokens} までに終わらなかった (推論で使い切った)")
            usage = {**res.get("usage", {}), "seconds": round(time.time() - t0, 1), "model": res.get("model")}
            return json.loads(text), usage
        except urllib.error.HTTPError as e:
            msg = e.read().decode("utf-8", "replace")[:300]
            if e.code in (429, 500, 502, 503) and i < retries - 1: time.sleep(5 * (i + 1)); continue
            raise RuntimeError(f"HTTP {e.code}: {msg}")
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            if i < retries - 1: time.sleep(5 * (i + 1)); continue
            raise
