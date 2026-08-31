#!/usr/bin/env python
"""Verify every external resource declared in .env / config.yaml actually works.

Run this BEFORE writing any pipeline code — a wrong deployment name or a missing
Postgres extension is much cheaper to find now than during ingest.

    python scripts/smoke_test.py             # everything
    python scripts/smoke_test.py db llm      # only named checks

Nothing here is hardcoded: deployment names, dimensions and endpoints are read
from config.yaml (which names the env var) and then from the environment. If a
check fails, the fix is a .env or config.yaml edit, never a code edit.

Exit code 0 = all required checks passed.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

try:
    import yaml
    from dotenv import load_dotenv
except ImportError:
    sys.exit("Missing deps. Activate the venv and: pip install -r requirements.txt")

load_dotenv(ROOT / ".env")
CONFIG = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))

# Windows consoles default to cp1252 and choke on arrows / box-drawing, and they
# do not render ANSI colour when output is piped. Degrade gracefully on both.
try:
    sys.stdout.reconfigure(encoding="utf-8")
    ARROW = "→"
except Exception:
    ARROW = "->"

_PLAIN = bool(os.environ.get("NO_COLOR")) or not sys.stdout.isatty()
GREEN, RED, YELLOW, DIM, RESET = (
    ("", "", "", "", "") if _PLAIN
    else ("\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m")
)
results: list[tuple[str, bool, str]] = []


def report(name: str, ok: bool, detail: str = "", required: bool = True) -> bool:
    icon = f"{GREEN}PASS{RESET}" if ok else (f"{RED}FAIL{RESET}" if required else f"{YELLOW}SKIP{RESET}")
    print(f"  [{icon}] {name}" + (f"  {DIM}{detail}{RESET}" if detail else ""))
    results.append((name, ok or not required, detail))
    return ok


def deployment_for(stage: str) -> tuple[str | None, str | None]:
    """config.yaml names the ENV VAR holding the deployment; resolve both."""
    spec = (CONFIG.get("models") or {}).get(stage) or {}
    provider = spec.get("provider") or os.environ.get(spec.get("provider_env", ""), "")
    dep_env = spec.get("deployment_env")
    return provider or None, (os.environ.get(dep_env) if dep_env else None)


# ─────────────────────────────────────────────────────────────────────────────
def check_env() -> None:
    print("\nENV / CONFIG")
    required = ["DATABASE_URL", "AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY"]
    for var in required:
        report(var, bool(os.environ.get(var)), "set" if os.environ.get(var) else "EMPTY")
    for stage in ("extractor", "verifier_a", "verifier_b", "embeddings", "reranker"):
        prov, dep = deployment_for(stage)
        report(f"config models.{stage}", bool(prov), f"{prov or '?'} {ARROW} {dep or '(endpoint)'}")

    # The independence question — surfaced loudly because it is a scoring risk.
    _, a = deployment_for("verifier_a")
    _, b = deployment_for("verifier_b")
    mode = (CONFIG.get("verification") or {}).get("independence")
    print(f"  {DIM}verifier independence: mode={mode}, A={a}, B={b}{RESET}")
    adversarial = (CONFIG.get("verification") or {}).get("verifier_b_adversarial")
    if not b:
        report("verifier independence", False,
               "verifier B unresolved - set VERIFIER_B_PROVIDER and VERIFIER_B_DEPLOYMENT in .env")
    elif a == b:
        # Same model is the current, deliberate state - but only valid when the
        # adversarial framing that substitutes for independence is actually on.
        report("verifier independence", mode == "same_model_adversarial" and bool(adversarial),
               "same model + adversarial framing (degraded, expected - HANDOFF 8e)"
               if mode == "same_model_adversarial" and adversarial
               else f"A and B are the SAME model but mode={mode}, adversarial={adversarial}")
    else:
        # A genuine second family: adversarial framing must be OFF, or the two
        # mechanisms stack and the system over-abstains.
        report("verifier independence", not adversarial,
               "distinct models" if not adversarial
               else "distinct models BUT adversarial still on - set verifier_b_adversarial: false")


def check_db() -> None:
    print("\nPOSTGRES")
    url = os.environ.get("DATABASE_URL")
    if not url:
        return report("connect", False, "DATABASE_URL unset") and None
    try:
        import psycopg
    except ImportError:
        return report("psycopg import", False, "pip install -r requirements.txt") and None

    try:
        t0 = time.time()
        with psycopg.connect(url, connect_timeout=10) as conn:
            report("connect", True, f"{(time.time()-t0)*1000:.0f} ms")
            with conn.cursor() as cur:
                cur.execute("SELECT version()")
                report("server", True, cur.fetchone()[0].split(",")[0])

                # Available vs installed — on Azure, availability needs the
                # azure.extensions allowlist before CREATE EXTENSION works.
                cur.execute("SELECT name FROM pg_available_extensions "
                            "WHERE name IN ('vector','pg_trgm','unaccent')")
                avail = {r[0] for r in cur.fetchall()}
                for ext in ("vector", "pg_trgm", "unaccent"):
                    report(f"extension {ext} available", ext in avail,
                           "" if ext in avail else "add to azure.extensions, then reconnect")

                cur.execute("SELECT extname FROM pg_extension")
                installed = {r[0] for r in cur.fetchall()}
                for ext in ("vector", "pg_trgm", "unaccent"):
                    report(f"extension {ext} installed", ext in installed,
                           "" if ext in installed else "run migrations/001_init.sql", required=False)

                cur.execute("SELECT to_regclass('public.pages'), to_regclass('public.filings')")
                pages, filings = cur.fetchone()
                report("schema applied", bool(pages and filings),
                       "tables present" if pages else "run: psql \"$DATABASE_URL\" -f migrations/001_init.sql",
                       required=False)

                # The embedding column must match EMBEDDING_DIMENSIONS exactly,
                # or every insert fails at ingest time.
                if pages:
                    cur.execute("""SELECT format_type(a.atttypid, a.atttypmod)
                                   FROM pg_attribute a
                                   WHERE a.attrelid='public.pages'::regclass AND a.attname='embedding'""")
                    row = cur.fetchone()
                    want = os.environ.get("EMBEDDING_DIMENSIONS", "1536")
                    got = row[0] if row else "?"
                    report("pages.embedding dimension", want in str(got),
                           f"schema={got}, EMBEDDING_DIMENSIONS={want}")
    except Exception as e:
        report("connect", False, f"{type(e).__name__}: {str(e)[:110]}")


def _azure_openai():
    """Azure exposes TWO client shapes and they are not interchangeable:

      a) classic  — AzureOpenAI(azure_endpoint="https://X.openai.azure.com/",
                                api_version=...)   the SDK appends /openai/...
      b) v1 API   — OpenAI(base_url="https://X.openai.azure.com/openai/v1/")
                                                   no api_version at all

    Passing a /openai/v1 URL to shape (a) yields 404s, so detect from the URL.
    """
    endpoint = (os.environ["AZURE_OPENAI_ENDPOINT"] or "").split("#")[0].strip()
    key = os.environ["AZURE_OPENAI_API_KEY"].strip()
    if "/openai/v1" in endpoint:
        from openai import OpenAI
        return OpenAI(base_url=endpoint.rstrip("/") + "/", api_key=key)
    from openai import AzureOpenAI
    return AzureOpenAI(
        azure_endpoint=endpoint,
        api_key=key,
        api_version=os.environ.get("AZURE_OPENAI_API_VERSION", "2024-10-21").split("#")[0].strip(),
    )


def check_llm() -> None:
    print("\nAZURE OPENAI — chat")
    _, dep = deployment_for("extractor")
    if not dep:
        return report("deployment resolved", False, "GPT_DEPLOYMENT unset") and None
    try:
        client = _azure_openai()
    except Exception as e:
        return report("client", False, f"{type(e).__name__}: {str(e)[:110]}") and None

    try:
        t0 = time.time()
        r = client.chat.completions.create(
            model=dep,
            messages=[{"role": "user", "content": "Reply with the single word: ok"}],
            max_completion_tokens=16,
        )
        report(f"chat {dep}", True,
               f"{(time.time()-t0)*1000:.0f} ms, {r.usage.total_tokens} tok")
    except Exception as e:
        return report(f"chat {dep}", False, f"{type(e).__name__}: {str(e)[:140]}") and None

    # Structured output is mandatory for every stage (§22.7) — if this fails the
    # pipeline needs a JSON-repair fallback path.
    try:
        schema = {"type": "object",
                  "properties": {"answer": {"type": "string"}},
                  "required": ["answer"], "additionalProperties": False}
        r = client.chat.completions.create(
            model=dep,
            messages=[{"role": "user", "content": "Return JSON with answer='ok'."}],
            response_format={"type": "json_schema",
                             "json_schema": {"name": "probe", "schema": schema, "strict": True}},
            # ⚠️ gpt-5-mini is a REASONING model: it spends completion tokens on
            # hidden reasoning BEFORE emitting content. A budget sized for the
            # visible answer alone returns content='' with finish_reason='length'
            # and no error. Measured: 64 reasoning tokens on a trivial prompt.
            max_completion_tokens=2000,
        )
        import json
        content = r.choices[0].message.content
        finish = r.choices[0].finish_reason
        rt = getattr(getattr(r.usage, "completion_tokens_details", None), "reasoning_tokens", None)
        if finish == "length" and not content:
            report("structured output (json_schema)", False,
                   f"empty content, finish_reason=length, reasoning_tokens={rt} "
                   f"- raise max_completion_tokens")
        else:
            json.loads(content)
            report("structured output (json_schema)", True,
                   f"strict mode OK (reasoning_tokens={rt})")
    except Exception as e:
        report("structured output (json_schema)", False,
               f"{type(e).__name__}: {str(e)[:140]} — may need a JSON-repair fallback")

    # The assembled candidate context must fit. Measured tier-1 budget is 42k.
    budget = (CONFIG.get("retrieval") or {}).get("assembly_token_budget", 42000)
    print(f"  {DIM}assembly_token_budget={budget:,} — confirm {dep}'s context window exceeds it{RESET}")


def check_embeddings() -> None:
    print("\nAZURE OPENAI — embeddings")
    spec = (CONFIG.get("models") or {}).get("embeddings") or {}
    dep = os.environ.get(spec.get("deployment_env", "EMBEDDING_DEPLOYMENT"))
    want = int(os.environ.get("EMBEDDING_DIMENSIONS") or spec.get("dimensions") or 1536)
    if not dep:
        return report("deployment resolved", False, "EMBEDDING_DEPLOYMENT unset") and None
    try:
        client = _azure_openai()
        t0 = time.time()
        try:                      # -3 models accept an explicit dimensions param
            r = client.embeddings.create(model=dep, input=["capital expenditures"],
                                         dimensions=want)
        except Exception:
            r = client.embeddings.create(model=dep, input=["capital expenditures"])
        got = len(r.data[0].embedding)
        report(f"embed {dep}", True, f"{got} dims, {(time.time()-t0)*1000:.0f} ms")
        report("dimension matches config", got == want, f"got {got}, expected {want}")
        report("fits pgvector hnsw (<=2000)", got <= 2000, f"{got} dims")
    except Exception as e:
        report(f"embed {dep}", False, f"{type(e).__name__}: {str(e)[:140]}")


def check_rerank() -> None:
    print("\nCOHERE RERANK")
    endpoint = os.environ.get("COHERE_RERANK_ENDPOINT")
    key = os.environ.get("COHERE_RERANK_API_KEY")
    if not (endpoint and key):
        return report("configured", False, "COHERE_RERANK_ENDPOINT/API_KEY unset",
                      required=False) and None
    try:
        import httpx
        url = endpoint.rstrip("/")
        if not url.endswith("/rerank"):
            url += "/v2/rerank" if "/v2" not in url else "/rerank"
        model = ((CONFIG.get("models") or {}).get("reranker") or {}).get("model")
        payload = {"query": "capital expenditures",
                   "documents": ["Purchases of property, plant and equipment (1,577)",
                                 "The Company operates in three segments."],
                   "top_n": 2}
        if model:
            payload["model"] = model
        t0 = time.time()
        resp = httpx.post(url, json=payload, timeout=30,
                          headers={"Authorization": f"Bearer {key}",
                                   "Content-Type": "application/json"})
        ok = resp.status_code == 200
        report(f"rerank {model or ''}".strip(), ok,
               f"{(time.time()-t0)*1000:.0f} ms" if ok else f"HTTP {resp.status_code}: {resp.text[:110]}")
        if ok:
            top = resp.json().get("results", [{}])[0]
            report("ranks the relevant doc first", top.get("index") == 0,
                   f"top index={top.get('index')}", required=False)
    except Exception as e:
        report("rerank", False, f"{type(e).__name__}: {str(e)[:140]}")


def check_anthropic() -> None:
    """Only runs if a Claude deployment is configured — currently we have no quota."""
    print("\nANTHROPIC ON FOUNDRY (verifier B second family)")
    prov, dep = deployment_for("verifier_b")
    if prov != "anthropic_foundry":
        return report("configured", False,
                      f"verifier_b provider is '{prov}' — no second family yet (HANDOFF §8e)",
                      required=False) and None
    try:
        from anthropic import AnthropicFoundry
        client = AnthropicFoundry(api_key=os.environ["AZURE_FOUNDRY_API_KEY"],
                                  resource=os.environ["AZURE_FOUNDRY_RESOURCE"])
        t0 = time.time()
        r = client.messages.create(model=dep, max_tokens=16,
                                   messages=[{"role": "user", "content": "Reply: ok"}])
        report(f"chat {dep}", True, f"{(time.time()-t0)*1000:.0f} ms")
    except Exception as e:
        report(f"chat {dep}", False, f"{type(e).__name__}: {str(e)[:140]}")


def check_data() -> None:
    print("\nDATA")
    filings = Path(os.environ.get("FILINGS_DIR", "../tac/analyst-copilot-data/filings"))
    if not filings.is_absolute():
        filings = (ROOT / filings).resolve()
    htm = list(filings.glob("*.htm")) if filings.exists() else []
    report("filings dir", len(htm) > 0, f"{len(htm)} .htm at {filings}")
    q = Path(os.environ.get("PRACTICE_QUESTIONS", "../tac/analyst-copilot-data/practice-questions.jsonl"))
    if not q.is_absolute():
        q = (ROOT / q).resolve()
    n = sum(1 for _ in q.open(encoding="utf-8")) if q.exists() else 0
    report("practice questions", n > 0, f"{n} questions")
    aliases = ROOT / "data" / "company_aliases.yaml"
    n_alias = len(yaml.safe_load(aliases.read_text(encoding="utf-8"))) if aliases.exists() else 0
    report("company aliases", n_alias > 0, f"{n_alias} companies")


CHECKS = {"env": check_env, "db": check_db, "llm": check_llm,
          "embeddings": check_embeddings, "rerank": check_rerank,
          "anthropic": check_anthropic, "data": check_data}

if __name__ == "__main__":
    wanted = sys.argv[1:] or list(CHECKS)
    unknown = [w for w in wanted if w not in CHECKS]
    if unknown:
        sys.exit(f"Unknown check(s): {unknown}. Available: {list(CHECKS)}")
    print("=" * 72)
    print("ANALYST COPILOT - resource smoke test")
    print("=" * 72)
    for name in wanted:
        CHECKS[name]()
    failed = [n for n, ok, _ in results if not ok]
    print("\n" + "=" * 72)
    if failed:
        print(f"{RED}{len(failed)} FAILED{RESET}: " + ", ".join(failed))
        print("Fix in .env or config.yaml — no code change should be needed.")
        sys.exit(1)
    print(f"{GREEN}All {len(results)} checks passed.{RESET}")
