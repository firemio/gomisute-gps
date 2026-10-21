"""DeepSeek などの LLM の呼び出し (OpenAI 互換の Chat Completions)。モデルと接続先は環境変数で切り替える

使う所: runners.py (資料の読み取り)・nationwide/discover/judge.py (ごみのページか見分ける)・nationwide/pilot/

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

def ask_json(system, parts, model=None, max_tokens=32000, retries=4, effort=None, raw=None):
    """system と、文字・画像の部品 → (JSON の答え, 使ったトークンなど)。JSON として読めなければ例外。
    raw に dict を渡すと、呼び出しごとの生の答え (content・reasoning_content・finish_reason・usage) を raw["attempts"] に足す
    (JSON として読めずに聞き直した分の費用も数えられるように)"""
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
            if raw is not None:
                raw.setdefault("attempts", []).append({
                    "content": text, "reasoning_content": ch["message"].get("reasoning_content"),
                    "finish_reason": ch.get("finish_reason"), "usage": res.get("usage"), "model": res.get("model"),
                    "seconds": round(time.time() - t0, 1)})
            if not text.strip() and ch.get("finish_reason") == "length":
                raise RuntimeError(f"答えが上限 max_tokens={max_tokens} までに終わらなかった (推論で使い切った)")
            usage = {**res.get("usage", {}), "seconds": round(time.time() - t0, 1), "model": res.get("model")}
            return json.loads(text), usage
        except urllib.error.HTTPError as e:
            msg = e.read().decode("utf-8", "replace")[:300]
            if raw is not None:
                raw.setdefault("attempts", []).append({"http_error": e.code, "message": msg})
            if e.code in (429, 500, 502, 503) and i < retries - 1: time.sleep(5 * (i + 1)); continue
            raise RuntimeError(f"HTTP {e.code}: {msg}")
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            if i < retries - 1: time.sleep(5 * (i + 1)); continue
            raise
