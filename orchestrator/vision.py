"""Cheap vision verifier — judge an image against fuzzy acceptance criteria.

Used for fuzzy/visual validation a deterministic command can't decide (e.g. "is this 3D
city block laid out correctly? anything floating/missing/overlapping?"). Default model is
gpt-4o-mini (cheap, vision-capable). The image is the EVIDENCE; the model returns a
VERDICT + 0-100 score so the orchestrator loop can gate on it.

Layered-validation intent: run a free pre-gate (pixel-diff / element exists) FIRST and only
call this when the artifact actually changed; escalate to a stronger model only on doubt.
"""
import base64
import json
import os
import re
import time
import urllib.request
import urllib.error

ENV_FILE = "/Users/marcos/projects/ai-gateway/.env"


def _env(name):
    v = os.environ.get(name)
    if v:
        return v
    try:
        for line in open(ENV_FILE):
            if line.startswith(name + "="):
                return line.split("=", 1)[1].strip()
    except FileNotFoundError:
        pass
    return None


def _provider():
    """Prefer OpenRouter (OpenAI-compatible, reaches gpt-4o-mini / gemini-flash cheaply)."""
    k = _env("OPENROUTER_API_KEY")
    if k:
        return ("https://openrouter.ai/api/v1/chat/completions", k, "openai/gpt-4o-mini")
    k = _env("OPENAI_API_KEY")
    if k:
        return ("https://api.openai.com/v1/chat/completions", k, "gpt-4o-mini")
    raise RuntimeError("no OPENROUTER_API_KEY / OPENAI_API_KEY (env or ai-gateway/.env)")


def judge_image(image_path: str, criteria: str, model: str = None,
                detail: str = "low") -> dict:
    """Return {ok, score, verdict, raw}. `detail='low'` keeps image tokens (and cost) down."""
    url, key, default_model = _provider()
    model = model or default_model
    with open(image_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    system = (
        "You are an INDEPENDENT, ADVERSARIAL visual QA judge. You did not create this "
        "image. Inspect it carefully and try to REFUTE that it meets the criteria. Be "
        "strict; default to FAIL if uncertain. Report concrete visual evidence.")
    user = (
        f"ACCEPTANCE CRITERIA:\n{criteria}\n\n"
        "Judge the image against EACH criterion. Then output, on the FINAL two lines ONLY:\n"
        "SCORE: <0-100>\n"
        "VERDICT: PASS   (or)   VERDICT: FAIL: <short reason>")
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": [
                {"type": "text", "text": user},
                {"type": "image_url",
                 "image_url": {"url": f"data:image/png;base64,{b64}", "detail": detail}},
            ]},
        ],
        "max_tokens": 500,
        "temperature": 0,
    }
    resp = None
    for attempt in range(4):
        req = urllib.request.Request(
            url, data=json.dumps(body).encode(),
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
        try:
            resp = json.loads(urllib.request.urlopen(req, timeout=90).read().decode())
            break
        except urllib.error.HTTPError as e:
            if e.code in (429, 502, 503) and attempt < 3:
                time.sleep(3 * (attempt + 1)); continue
            raise RuntimeError(f"vision API HTTP {e.code}: {e.read().decode()[:200]}")
    raw = resp["choices"][0]["message"]["content"]
    ok = bool(re.search(r"VERDICT:\s*PASS\b", raw))
    m = re.search(r"SCORE:\s*(\d+)", raw)
    score = int(m.group(1)) if m else None
    return {"ok": ok, "score": score, "verdict": raw.strip().splitlines()[-1], "raw": raw}


if __name__ == "__main__":
    import sys
    r = judge_image(sys.argv[1], sys.argv[2])
    print(json.dumps({k: r[k] for k in ("ok", "score", "verdict")}, ensure_ascii=False))
