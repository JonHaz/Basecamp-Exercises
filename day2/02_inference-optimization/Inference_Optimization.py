# %% [markdown]
# # Inference Optimization Lab — **The Margin Call**
#
# Your firm signed **Project HELVETICA**: an AI-assisted contract-diligence engagement for
# **Aldgate Capital Partners**, who are acquiring Volta Industrial Group — a roll-up of 14
# companies with a supplier-contract estate of **248,000 documents**. The deal team needs every
# contract triaged for change-of-control consent, liability caps, auto-renewal, and governing law.
#
# The demo team built an agent — **ClauseScan v0** — the night before the pitch. It works.
# It is also slow enough that analysts alt-tab away while it thinks, and its unit economics
# quietly eat the engagement margin. The pitch landed, the SOW is signed, and as of this
# morning **you own it**.
#
# What the client signed:
#
# | SLA term | Commitment |
# |---|---|
# | Accuracy | ≥ 90% on audited fields |
# | Interactive latency | p50 ≤ 5s per contract in analyst working sessions |
# | Unit economics | COGS ≤ $0.02 per contract at production scale |
# | Fee | $0.75 per reviewed contract (fixed) |
#
# **The lab in three acts:**
# 1. **Parts 1–2 — Instrument & baseline.** Build the measurement toolkit, run the initial tests
#    across Haiku / Sonnet / Opus.
# 2. **Parts 3–4 — Diagnose & learn the levers.** Run ClauseScan v0, see the damage, then work
#    through six optimization levers one at a time, measuring each.
# 3. **Parts 5–6 — The optimization sprint.** Rebuild the pipeline, climb the leaderboard, and
#    auto-generate the before/after slide you'd take to the steering committee.
#
# **Key metrics:**
# - **TTFT** — Time To First Token: how long the analyst stares at a spinner.
# - **TTC** — Time To Completion: total request duration.
# - **OTPS** — Output Tokens Per Second: generation throughput after streaming starts.
# - **$/contract** — fully loaded cost per document, including cache reads and writes.
#
# > 💸 Running every cell in this notebook costs roughly **$2–4** of API usage, most of it in the
# > deliberately wasteful v0 baseline. That's part of the lesson.
#
# ---
#
# **Prepared for Partner Basecamp participants.** Not for reproduction or redistribution as training material — you're free to apply these patterns in your own client work.

# %% [markdown]
# # Part 0 · Setup
#
# Dependencies first, then your API key. Never paste a key into a cell — you will paste
# notebooks into client repos one day; build the habit now.

# %%
# Install dependencies into THIS kernel — safe to re-run; survives locked-down (PEP 668) Pythons.
import importlib.util, subprocess, sys

def _ensure_packages(requirements):
    """requirements: list of (import_name, pip_spec). Install only what is missing,
    into the running interpreter. Tries a normal install, then user-space, then a
    PEP 668 override (user-space first, system-wide only as a last resort). Every
    attempt is silent — pip's output is captured, not streamed — so a locked-down
    Python (Homebrew or Debian, PEP 668) no longer dumps a scary
    'externally-managed-environment' wall of text when a fallback is what actually
    succeeds. Only if every strategy fails does it surface the reason, with the
    venv fix instead of a raw traceback."""
    missing = [pip for mod, pip in requirements if importlib.util.find_spec(mod) is None]
    if not missing:
        return
    print("Installing " + ", ".join(missing) + " — first run only, please wait…", flush=True)
    base = [sys.executable, "-m", "pip", "install", "-q"]
    last = None
    for extra in ([], ["--user"], ["--user", "--break-system-packages"], ["--break-system-packages"]):
        last = subprocess.run(base + extra + missing, capture_output=True, text=True)
        if last.returncode == 0:
            return
    pip_said = (last.stderr or last.stdout or "").strip().splitlines() if last else []
    tail = "\n      ".join(pip_said[-3:]) if pip_said else "(no output from pip)"
    raise SystemExit(
        "\n  Couldn't install: " + ", ".join(missing) + "\n"
        "  This Python is locked down (PEP 668) or offline. Quickest fix is a venv:\n"
        f"      {sys.executable} -m venv .venv\n"
        "      source .venv/bin/activate          # Windows: see SETUP.md\n"
        f"      pip install {' '.join(missing)}\n"
        "  Then pick the .venv interpreter in VS Code (kernel picker, top-right) and Run All.\n"
        "  Corporate proxy or PyPI blocked? See SETUP.md in the repo root.\n"
        f"  (pip said: {tail})\n"
    )

_ensure_packages([("anthropic", "anthropic"), ("tabulate", "tabulate")])
print("✓ Dependencies ready")

import os
import json
import time
import hashlib
import pathlib
import statistics
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import List, Optional
from concurrent.futures import ThreadPoolExecutor
from tabulate import tabulate

import anthropic

# %% [markdown]
# ### Setup — connect to Claude
#
# Run the next cell first. The setup cell creates a **`.env` file** the first time you run it (gitignored — your key
# is never committed). Open it, paste your key after `ANTHROPIC_API_KEY=`, save, and re-run —
# it survives kernel restarts, so you paste once. *(No `.env` yet? A hidden input box appears
# as a fallback.)* You're locked in when you see the green **"✓ API key verified"** banner. Red banner? Do what it
# says and run the cell again.

# %%
import os

def _status(ok, msg):
    """Green/red banner in notebooks; plain text when run as a script."""
    try:
        from IPython import get_ipython
        shell = get_ipython()
        if shell is None or shell.__class__.__name__ != "ZMQInteractiveShell":
            raise RuntimeError("not in a notebook kernel - use the plain-text banner")
        from IPython.display import display, HTML
        color = "#1a7f37" if ok else "#b42318"
        bg = "#e6f4ea" if ok else "#fdecea"
        icon = "✓" if ok else "✗"
        display(HTML(
            f'<div style="padding:12px 16px;border-radius:8px;background:{bg};'
            f'border:1.5px solid {color};color:{color};font-weight:600;'
            f'font-size:15px;font-family:sans-serif;">{icon} {msg}</div>'
        ))
    except Exception:
        print(("[OK] " if ok else "[!!] ") + msg)

import os
import pathlib

import anthropic

# ── Connect to Claude — Anthropic API or Amazon Bedrock ──
# Works with either credential type; the cell figures out which you have.
#   Anthropic API : ANTHROPIC_API_KEY=sk-ant-...
#   Amazon Bedrock: AWS_BEARER_TOKEN_BEDROCK=...  plus  AWS_REGION=us-east-1
# Put whichever you use in the .env file this cell creates (gitignored — never committed),
# or export it in your shell. A value in the shell wins over the .env file.
_ENV_TEMPLATE = (
    "# Anthropic API key — paste after the = (no quotes, no spaces), then save and\n"
    "# re-run the setup cell. Get one at https://console.anthropic.com/\n"
    "ANTHROPIC_API_KEY=paste-your-key-here\n"
    "\n"
    "# --- Using Amazon Bedrock instead? Comment out the line above and fill these in:\n"
    "# AWS_BEARER_TOKEN_BEDROCK=paste-your-bedrock-api-key-here\n"
    "# AWS_REGION=us-east-1\n"
)


def _resolve_env_file():
    """Nearest existing .env walking up from the working dir (so one root .env serves every
    exercise); if none exists yet, point at the repo root — or this folder if the notebook
    was opened on its own."""
    here = pathlib.Path.cwd().resolve()
    for d in [here, *here.parents]:
        if (d / ".env").is_file():
            return d / ".env"
    root = next((d for d in [here, *here.parents]
                 if (d / "SETUP.md").exists() or (d / ".git").exists()), here)
    return root / ".env"


_env_file = _resolve_env_file()
if not _env_file.exists():
    _env_file.write_text(_ENV_TEMPLATE)
    print(f"Created {_env_file.name} in {_env_file.parent} — open it, add your key, "
          "save, then re-run this cell.")

# Tiny .env parser (no python-dotenv dependency). Re-read on every run, so pasting your
# key and re-running picks it up. A real value in the environment (shell / Claude Code / CI)
# wins; the placeholder never sticks.
_file = {}
for _line in (_env_file.read_text().splitlines() if _env_file.exists() else []):
    _line = _line.strip()
    if _line and not _line.startswith("#") and "=" in _line:
        _k, _v = _line.split("=", 1)
        _file[_k.strip()] = _v.strip().strip('"').strip("'")
for _k, _v in _file.items():
    if _k != "ANTHROPIC_API_KEY":
        os.environ.setdefault(_k, _v)

_shell_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
_anthropic_key = _shell_key if _shell_key.startswith("sk-ant-") else _file.get("ANTHROPIC_API_KEY", "").strip()
_bedrock_token = os.environ.get("AWS_BEARER_TOKEN_BEDROCK", "").strip()
_bedrock_region = os.environ.get("AWS_REGION", "").strip()

if _anthropic_key.startswith("sk-ant-"):
    PROVIDER = "anthropic"
elif _bedrock_token:
    PROVIDER = "bedrock"
else:
    PROVIDER = None


def _needs_credentials(head, body):
    """Warning-yellow banner + stop, so setup fails here rather than several cells later."""
    _shown = False
    try:
        from IPython import get_ipython
        if get_ipython().__class__.__name__ == "ZMQInteractiveShell":
            import html as _html
            from IPython.display import HTML, display
            display(HTML(
                '<div style="padding:12px 16px;border-radius:8px;background:#fff8c5;'
                'border:1.5px solid #9a6700;font-size:15px;font-family:sans-serif;">'
                '<div style="color:#9a6700;font-weight:600;">' + _html.escape(head) + '</div>'
                '<pre style="margin:10px 0 0;font-family:inherit;font-size:14px;font-weight:400;'
                'color:#141413;white-space:pre-wrap;">' + _html.escape(body) + '</pre></div>'
            ))
            _shown = True
    except Exception:
        pass
    if not _shown:
        print("\n" + head + ":\n   " + body.replace("\n", "\n   ") + "\n")
    raise SystemExit("Credentials missing — see the message above.")


if PROVIDER is None:
    _needs_credentials(
        "📋 Add your credentials to continue",
        f"Open this file:  {_env_file}\n"
        "\n"
        "Using the Anthropic API? Set:\n"
        "    ANTHROPIC_API_KEY=sk-ant-...\n"
        "\n"
        "Using Amazon Bedrock? Set both:\n"
        "    AWS_BEARER_TOKEN_BEDROCK=<your Bedrock API key>\n"
        "    AWS_REGION=us-east-1          # the region your models are enabled in\n"
        "\n"
        "Save the file, then click ▶ on this cell again."
    )

if PROVIDER == "bedrock" and not _bedrock_region:
    _needs_credentials(
        "📋 Bedrock needs a region",
        f"Found AWS_BEARER_TOKEN_BEDROCK but no AWS_REGION.\n"
        f"\n"
        f"Open this file:  {_env_file}\n"
        "and add the region your Bedrock models are enabled in, e.g.:\n"
        "    AWS_REGION=us-east-1\n"
        "\n"
        "Save the file, then click ▶ on this cell again."
    )


def _model(name):
    """Bedrock model IDs carry an `anthropic.` prefix; the Anthropic API uses the bare ID."""
    return f"anthropic.{name}" if PROVIDER == "bedrock" else name


# Named models the exercise uses — resolved for whichever provider you're on.
MODEL = _model("claude-sonnet-5")        # the workhorse for this exercise
FAST_MODEL = _model("claude-haiku-4-5")  # cheap + quick (connection check, judges)
BIG_MODEL = _model("claude-opus-4-8")    # when you want to try a larger model


def _make_client(timeout, max_retries=2):
    if PROVIDER == "bedrock":
        from anthropic import AnthropicBedrockMantle
        return AnthropicBedrockMantle(aws_region=_bedrock_region,
                                      timeout=timeout, max_retries=max_retries)
    return anthropic.Anthropic(api_key=_anthropic_key,
                               timeout=timeout, max_retries=max_retries)


# Connection check — verifies the credential AND that this model is reachable for you.
# On Bedrock a valid key can still 404 if the model isn't enabled in your account/region,
# so we ping the real model ID rather than just checking the credential's shape.
_probe = _make_client(timeout=30.0, max_retries=1)
try:
    _probe.messages.create(model=FAST_MODEL, max_tokens=1,
                           messages=[{"role": "user", "content": "ping"}])
except anthropic.NotFoundError:
    if PROVIDER == "bedrock":
        _status(False, f"Bedrock reached, but model '{FAST_MODEL}' isn't available to you in "
                       f"{_bedrock_region}. Enable model access for it in the Bedrock console "
                       f"(or switch AWS_REGION to a region where it is enabled), then re-run.")
    else:
        _status(False, f"Model '{FAST_MODEL}' not found for this key.")
    raise SystemExit("Model not available — see the message above.")
except (anthropic.AuthenticationError, anthropic.PermissionDeniedError):
    if PROVIDER == "bedrock":
        _status(False, "That Bedrock key was rejected. Check AWS_BEARER_TOKEN_BEDROCK and that "
                       "it has Bedrock invoke permissions, then run this cell again.")
    else:
        _status(False, "That key was rejected. Run this cell again and paste the whole key "
                       "(it starts with sk-ant-).")
    raise SystemExit("Credentials not accepted - re-run this cell and try again.")
except Exception as exc:
    _status(False, "Could not reach the API (" + type(exc).__name__ + "). Check your "
                   "connection, then run this cell again.")
    raise
else:
    if PROVIDER == "anthropic":
        os.environ["ANTHROPIC_API_KEY"] = _anthropic_key  # later cells / !python pick it up
        _status(True, "API key verified - you're connected to Claude.")
    else:
        _status(True, f"Bedrock key verified ({_bedrock_region}) - you're connected to Claude "
                      f"as {MODEL}.")

# The working client. Longer timeout: needed for max_tokens>21333 with non-streaming calls.
client = _make_client(timeout=900.0)

# %%
# The lab client was created by the setup block above with a long timeout — the short
# 30s probe client is fine for a 1-token ping but fatal for v0's deliberately slow,
# non-streamed essay calls. It's already the right one; nothing to re-create here.

# The model portfolio. Aliases, not date-pinned IDs — aliases track the current snapshot
# and don't 404 when a snapshot is retired. Resolved for your provider (Anthropic API or
# Amazon Bedrock) in the setup block above.
MODEL_HAIKU = FAST_MODEL
MODEL_SONNET = MODEL
MODEL_OPUS = BIG_MODEL

print(f"SDK {anthropic.__version__} · portfolio: {MODEL_HAIKU}, {MODEL_SONNET}, {MODEL_OPUS}")

# %% [markdown]
# # Part 1 · The instrument panel
#
# You cannot optimize what you cannot measure, and you cannot bill a client for an improvement
# you cannot prove. Everything in this lab flows through one streaming helper and one
# cache-aware cost function.

# %%
@dataclass
class BenchmarkResult:
    """Timing, tokens, and cost for a single API call."""
    ttft: float                    # Time to First Token (seconds)
    total_time: float              # Time to Completion (seconds)
    input_tokens: int
    output_tokens: int
    model: str
    test_name: str
    otps: Optional[float] = None   # Output Tokens Per Second
    cost: Optional[float] = None   # Dollars, cache-aware
    cache_read: int = 0
    cache_write: int = 0


@dataclass
class BenchmarkSuite:
    """Collects results across runs and prints a comparison table."""
    results: List[BenchmarkResult] = field(default_factory=list)

    def add(self, result: BenchmarkResult):
        self.results.append(result)

    def clear(self):
        self.results = []

    def summary(self) -> str:
        if not self.results:
            return "No results."
        groups: dict = {}
        for r in self.results:
            groups.setdefault(r.test_name, []).append(r)
        rows = []
        for name, group in groups.items():
            rows.append([
                name,
                len(group),
                f"{statistics.mean(r.ttft for r in group) * 1000:.0f}",
                f"{statistics.mean(r.total_time for r in group) * 1000:.0f}",
                f"{statistics.mean(r.otps or 0 for r in group):.1f}",
                f"${sum(r.cost or 0 for r in group) * 1000:.2f}",
            ])
        headers = ["Test", "Runs", "TTFT(ms)", "TTC(ms)", "OTPS", "$/1K calls"]
        return tabulate(rows, headers=headers, tablefmt="grid")


suite = BenchmarkSuite()
print("BenchmarkSuite ready")

# %% [markdown]
# **The streaming helper.** Streaming is the default posture for anything user-facing: the
# analyst sees tokens immediately (TTFT) instead of waiting for the whole response (TTC), and
# long responses can't hit HTTP timeouts. We watch the event stream for the first
# `content_block_start` to stamp TTFT, then collect the final message.

# %%
def _stream_request(messages, model, max_tokens=1024, system=None, **kwargs):
    """Stream a request; return (ttft_seconds, total_seconds, final_message)."""
    ttft = None
    params = dict(model=model, max_tokens=max_tokens, messages=messages, **kwargs)
    if system is not None:
        params["system"] = system
    start = time.perf_counter()
    with client.messages.stream(**params) as stream:
        for event in stream:
            if ttft is None and event.type == "content_block_start":
                ttft = time.perf_counter() - start
        response = stream.get_final_message()
    total = time.perf_counter() - start
    return ttft if ttft is not None else total, total, response


def compute_otps(ttft, total_time, output_tokens):
    """Output Tokens Per Second, measured over generation time (TTC minus TTFT).
    Time before the first token is waiting, not generating."""
    gen_time = max(total_time - ttft, 1e-9)
    return output_tokens / gen_time, gen_time


def text_of(response) -> str:
    return "".join(b.text for b in response.content if b.type == "text")


ttft, total, resp = _stream_request(
    [{"role": "user", "content": "What is 2 + 2? Answer in one word."}], model=MODEL_SONNET)
otps, gen = compute_otps(ttft, total, resp.usage.output_tokens)
print(f"Response: {text_of(resp).strip()}")
print(f"TTFT {ttft*1000:.0f}ms · TTC {total*1000:.0f}ms · OTPS {otps:.1f} tok/s")

# %% [markdown]
# **Cache-aware cost.** Most cost functions you'll see in the wild price `input × rate +
# output × rate` and stop. That misprices any cached workload badly: cache **writes** bill at
# **1.25×** the input rate (5-minute TTL) and cache **reads** at **0.1×**. When you stand in
# front of a client CFO, your unit economics need all four terms.
#
# | Model | Input $/MTok | Output $/MTok |
# |---|---|---|
# | Haiku 4.5 | $1.00 | $5.00 |
# | Sonnet 5 | $2.00 | $10.00 |
# | Opus 4.8 | $5.00 | $25.00 |
#
# *(Re-verified 2026-08-28 against the live pricing page — the shipped table had Sonnet at
# $3.00/$15.00, which is the **Sonnet 4.6** rate. See the decision log below. Batch API runs at
# 50% of everything; that lever arrives in Part 4.)*

# %% [markdown]
# ### 🧭 Decision log — the cost model is a claim, and claims get verified
#
# Before optimizing anything, audit the instrument. The shipped `PRICING` table is hardcoded and
# self-dated "June 2026", and one row is wrong.
#
# | Model | Notebook shipped | Verified 2026-08-28 | |
# |---|---|---|---|
# | Haiku 4.5 | $1.00 / $5.00 | $1.00 / $5.00 | ✅ |
# | Sonnet 5 | **$3.00 / $15.00** | **$2.00 / $10.00** | ⚠️ corrected |
# | Opus 4.8 | $5.00 / $25.00 | $5.00 / $25.00 | ✅ |
#
# **Source:** `platform.claude.com/docs/en/about-claude/pricing`, fetched 2026-08-28. $3/$15 is the
# **Sonnet 4.6** rate — a stale row carried forward onto a Sonnet 5 constant. The page also notes
# that Sonnet 5's $2/$10 launch pricing became the standard price, and the increase to $3/$15 once
# scheduled for 2026-09-01 was cancelled.
#
# **Why this is not pedantry.** Cost enters this lab twice: as a **ratio** in the leaderboard score,
# and as an **absolute** in the steering-committee slide. Sonnet is the workhorse of every optimized
# config while the v0 baseline is Opus — which was priced correctly. So the stale row overpriced the
# numerator and not the denominator: it *understated* every optimized score by up to 1.5× on the cost
# half, and *overstated* the per-contract COGS quoted to the client. A wrong price table doesn't fail
# loudly; it just makes you lose the leaderboard and over-quote the CFO at the same time.
#
# The notebook's own markdown says "verify against the pricing page before quoting in a deliverable."
# This is that instruction, actually followed.
#
# **Also recorded here:** the 1-hour cache TTL write multiplier (2.0×, vs 1.25× for 5-minute). The
# shipped table only carries the 5-minute figure, which silently assumes an interactive workload. The
# overnight batch lane in Part 4 is the case where 1h is the right choice — see Lever 1.

# %%
# Rates verified 2026-08-28 against platform.claude.com/docs/en/about-claude/pricing.
# The Sonnet row shipped as 3.00/15.00 — that is the Sonnet 4.6 rate. Corrected. See the
# decision log above for why a stale row here costs you twice.
PRICING = {
    MODEL_HAIKU:  {"input": 1.00, "output":  5.00},   # verified 2026-08-28
    MODEL_SONNET: {"input": 2.00, "output": 10.00},   # verified 2026-08-28 (was 3.00/15.00)
    MODEL_OPUS:   {"input": 5.00, "output": 25.00},   # verified 2026-08-28
}
PRICING_VERIFIED_ON = "2026-08-28"
PRICING_SOURCE = "platform.claude.com/docs/en/about-claude/pricing"

CACHE_WRITE_MULT = 1.25      # 5-minute TTL cache write  — pays back after ~1 read
CACHE_WRITE_MULT_1H = 2.00   # 1-hour TTL cache write    — pays back after ~2 reads
CACHE_READ_MULT = 0.10
BATCH_DISCOUNT = 0.50        # Batch API: 50% off all token charges


def calculate_cost(model: str, usage, cache_ttl: str = "5m", batch: bool = False) -> float:
    """Fully loaded cost in dollars for one API call, including cache economics.

    cache_ttl: "5m" (1.25x write) or "1h" (2.0x write). Defaults to 5m to match the
               shipped behaviour — but the default is now an explicit choice, not an
               unstated assumption baked into a constant.
    batch:     apply the Batch API 50% discount to every term.
    """
    p = PRICING[model]
    write_mult = CACHE_WRITE_MULT_1H if cache_ttl == "1h" else CACHE_WRITE_MULT
    cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
    cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
    cost = (
        usage.input_tokens * p["input"]
        + cache_write * p["input"] * write_mult
        + cache_read * p["input"] * CACHE_READ_MULT
        + usage.output_tokens * p["output"]
    ) / 1e6
    return cost * BATCH_DISCOUNT if batch else cost


def measure(prompt: str, model: str, test_name: str, max_tokens: int = 256) -> BenchmarkResult:
    """One measured streaming call → a BenchmarkResult."""
    ttft, total, resp = _stream_request(
        [{"role": "user", "content": prompt}], model=model, max_tokens=max_tokens)
    otps, _ = compute_otps(ttft, total, resp.usage.output_tokens)
    return BenchmarkResult(
        ttft=ttft, total_time=total,
        input_tokens=resp.usage.input_tokens, output_tokens=resp.usage.output_tokens,
        model=model, test_name=test_name, otps=otps,
        cost=calculate_cost(model, resp.usage),
        cache_read=getattr(resp.usage, "cache_read_input_tokens", 0) or 0,
        cache_write=getattr(resp.usage, "cache_creation_input_tokens", 0) or 0,
    )


print(f"Pricing table verified {PRICING_VERIFIED_ON} against {PRICING_SOURCE}")
print(f"Cost of the 2+2 call above: ${calculate_cost(MODEL_SONNET, resp.usage):.6f}")

# %% [markdown]
# # Part 2 · Initial tests — the model portfolio
#
# Same prompt, three models, four runs each. This is the single biggest lever you have, so it
# goes first: before optimizing *how* you call a model, decide *which* model each piece of work
# deserves.

# %%
PROBE = "What is machine learning? Answer in 2 sentences."

suite.clear()
for model_id, label in [(MODEL_HAIKU, "haiku"), (MODEL_SONNET, "sonnet"), (MODEL_OPUS, "opus")]:
    print(f"Benchmarking {label}...")
    for i in range(4):
        r = measure(PROBE, model=model_id, test_name=label)
        suite.add(r)
        print(f"  run {i+1}: TTFT {r.ttft*1000:.0f}ms · TTC {r.total_time*1000:.0f}ms"
              f" · OTPS {r.otps:.1f} · ${r.cost:.6f}")

print()
print(suite.summary())

# %% [markdown]
# **Read the table like a partner, not a benchmark blog.** Haiku is typically several times
# cheaper and noticeably faster to first token; Opus buys depth you only need on hard cases.
# The right architecture is almost never "pick one" — it's a **portfolio**: cheap models for
# high-volume routine work, expensive models for the judgment calls, and a router deciding
# which is which. Hold that thought; it becomes Lever 2.

# %% [markdown]
# # Part 3 · The engagement — meet ClauseScan v0
#
# Below is the lab-scale version of the HELVETICA workload: a six-contract sample of the Volta
# supplier estate (production contracts are ~8× longer — we extrapolate honestly in Part 6),
# the firm's **diligence playbook** (the standards document every reviewer follows — long, and
# identical on every single call), **gold labels** an associate prepared by hand, and a grader.
#
# Then we run the agent you inherited, exactly as the demo team wrote it. Run it before
# reading ahead — the smell test is part of the job.

# %%
# ── The contract sample (lab-scale stand-ins for the 248K-document estate) ──────────────
CONTRACTS = [
    {
        "id": "C-101", "vendor": "NorthWind Logistics GmbH",
        "text": """MASTER SERVICES AGREEMENT — NorthWind Logistics GmbH ("Supplier") and Volta Industrial Group ("Customer").

1. SERVICES. Supplier provides freight forwarding and warehouse management services per attached SOWs.
2. TERM AND RENEWAL. Initial term of twenty-four (24) months. This Agreement automatically renews for successive twelve (12) month periods unless either party gives written notice of non-renewal at least sixty (60) days before the end of the then-current term.
3. FEES AND PAYMENT. Fees per Schedule A. Invoices payable net forty-five (45) days. Late amounts accrue interest at 1% per month.
4. LIMITATION OF LIABILITY. EXCEPT FOR BREACHES OF CONFIDENTIALITY, EACH PARTY'S AGGREGATE LIABILITY ARISING OUT OF THIS AGREEMENT SHALL NOT EXCEED TWO HUNDRED FIFTY THOUSAND U.S. DOLLARS ($250,000).
5. ASSIGNMENT. Either party may assign this Agreement to an affiliate or in connection with a merger or sale of substantially all assets without the consent of the other party, upon written notice.
6. CONFIDENTIALITY. Standard mutual obligations for 3 years post-termination.
7. GOVERNING LAW. This Agreement is governed by the laws of the State of Delaware, without regard to conflicts of law principles.""",
    },
    {
        "id": "C-102", "vendor": "Apex Facilities Co.",
        "text": """FACILITIES MAINTENANCE AGREEMENT — Apex Facilities Co. ("Contractor") and Volta Industrial Group ("Client").

1. SCOPE. HVAC, electrical, and janitorial maintenance for the sites listed in Exhibit 1.
2. TERM. Fixed term of thirty-six (36) months from the Effective Date. This Agreement expires at the end of the term and does not renew automatically; any extension requires a written amendment signed by both parties.
3. PRICING. Monthly fixed fee of $18,400 plus materials at cost +10%.
4. LIMITATION OF LIABILITY. Contractor's total cumulative liability under this Agreement shall not exceed ONE HUNDRED THOUSAND U.S. DOLLARS ($100,000). Neither party is liable for indirect or consequential damages.
5. ASSIGNMENT. Either party may freely assign this Agreement, including in connection with any change of control, without consent.
6. INSURANCE. Contractor maintains commercial general liability coverage of $2,000,000 per occurrence.
7. GOVERNING LAW. The laws of the State of New York govern this Agreement.""",
    },
    {
        "id": "C-103", "vendor": "Cobalt Data Services Ltd.",
        "text": """DATA PROCESSING AND HOSTING AGREEMENT — Cobalt Data Services Ltd. ("Provider") and Volta Industrial Group ("Company").

1. SERVICES. Provider hosts Company's plant telemetry platform and processes operational data.
2. TERM AND RENEWAL. Initial term of twelve (12) months, automatically renewing for successive one-year terms unless either party provides ninety (90) days' written notice of termination.
3. SERVICE LEVELS. 99.9% monthly uptime; service credits per Schedule 2.
4. LIMITATION OF LIABILITY. PROVIDER'S AGGREGATE LIABILITY SHALL NOT EXCEED FIVE HUNDRED THOUSAND U.S. DOLLARS ($500,000) OR THE FEES PAID IN THE PRIOR 12 MONTHS, WHICHEVER IS GREATER, PROVIDED THE CAP SHALL IN NO EVENT EXCEED $500,000.
5. ASSIGNMENT AND CHANGE OF CONTROL. Neither party may assign this Agreement, whether by operation of law, change of control, merger, or otherwise, without the prior written consent of the other party, such consent not to be unreasonably withheld.
6. DATA PROTECTION. Provider processes personal data per the DPA in Exhibit C.
7. GOVERNING LAW. This Agreement is governed by the laws of the State of California.""",
    },
    {
        "id": "C-104", "vendor": "Ironclad Security Services plc",
        "text": """SECURITY SERVICES AGREEMENT — Ironclad Security Services plc ("Ironclad") and Volta Industrial Group ("Principal").

1. SERVICES. Manned guarding, alarm response, and access control for Principal's UK sites.
2. TERM. Twelve (12) months from the Commencement Date, terminating automatically at expiry. Renewal only by mutual written agreement.
3. INDEMNITY. Ironclad indemnifies Principal against third-party claims arising from Ironclad's negligence; Principal indemnifies Ironclad against claims arising from site conditions not disclosed in the Site Survey. Each indemnity is uncapped and survives termination for six (6) years.
4. LIABILITY. The parties acknowledge the indemnities in Section 3. This Agreement does not otherwise state any cap, ceiling, or other limitation on either party's liability.
5. ASSIGNMENT AND CHANGE OF CONTROL. Ironclad may terminate this Agreement on thirty (30) days' notice upon any change of control of Principal. Any assignment by either party requires the prior written consent of the other.
6. TUPE. The parties acknowledge the potential application of TUPE regulations to guard personnel on expiry.
7. GOVERNING LAW. This Agreement and any dispute arising out of it are governed by the laws of England and Wales.""",
    },
    {
        "id": "C-105", "vendor": "Helios Components S.A.",
        "text": """SUPPLY AGREEMENT — Helios Components S.A. ("Helios") and Volta Industrial Group ("Buyer").

1. SUPPLY. Helios supplies the precision components listed in Annex 1 per Buyer's purchase orders.
2. TERM AND RENEWAL. Initial term of twenty-four (24) months, automatically extending for successive twelve-month periods unless either party gives one hundred twenty (120) days' notice.
3. PRICING. Annex 2 price list, adjusted annually per the PPI index, capped at 4% per year.
4. LIMITATION OF LIABILITY. Subject to Section 9 (IP Indemnity), each party's aggregate liability under this Agreement shall not exceed ONE MILLION U.S. DOLLARS ($1,000,000).
5. ASSIGNMENT AND CHANGE OF CONTROL. Any direct or indirect change of control of Buyer requires Helios's prior written consent. Helios may withhold consent in its sole discretion.
6. QUALITY. Components conform to the specifications in Annex 3; non-conforming lots replaced at Helios's cost.
7. GOVERNING LAW. This Agreement is governed by the laws of the State of Texas.

AMENDMENT NO. 2 (executed and effective). The parties agree as follows: Section 4 (Limitation of Liability) of the Agreement is deleted in its entirety and shall be of no further force or effect. For the avoidance of doubt, following this Amendment the Agreement states no cap on either party's liability. All other terms remain unchanged.""",
    },
    {
        "id": "C-106", "vendor": "Brightline Staffing LLC",
        "text": """STAFFING SERVICES AGREEMENT — Brightline Staffing LLC ("Brightline") and Volta Industrial Group ("Client").

1. SERVICES. Brightline supplies temporary production and warehouse personnel on request.
2. TERM AND RENEWAL. One (1) year initial term, renewing automatically for successive one-year terms unless either party gives thirty (30) days' written notice prior to renewal.
3. RATES. Bill rates per Rate Card v7; overtime at 1.5×; conversion fee of 20% of first-year salary for direct hires within 12 months.
4. WORKER CLASSIFICATION. Brightline is the employer of record and is responsible for wages, withholding, and workers' compensation coverage.
5. ASSIGNMENT. Either party may assign this Agreement without the other party's consent, including in connection with a change of control, provided the assignee assumes all obligations.
6. WARRANTIES. Services performed in a professional and workmanlike manner. THE PARTIES HAVE NOT AGREED ANY LIMITATION OR CAP ON LIABILITY UNDER THIS AGREEMENT.
7. GOVERNING LAW. This Agreement is governed by the laws of the State of Illinois.""",
    },
]

# Gold labels — prepared by hand, the way an associate would on a real engagement.
# risk_tier rule (also stated in the playbook): HIGH = change-of-control consent required AND
# no liability cap; MEDIUM = exactly one of those red flags; LOW = neither.
GOLD = {
    "C-101": {"auto_renewal": True,  "change_of_control": False, "liability_cap_usd": 250000,
              "governing_law": "Delaware",          "risk_tier": "LOW"},
    "C-102": {"auto_renewal": False, "change_of_control": False, "liability_cap_usd": 100000,
              "governing_law": "New York",          "risk_tier": "LOW"},
    "C-103": {"auto_renewal": True,  "change_of_control": True,  "liability_cap_usd": 500000,
              "governing_law": "California",        "risk_tier": "MEDIUM"},
    "C-104": {"auto_renewal": False, "change_of_control": True,  "liability_cap_usd": None,
              "governing_law": "England and Wales", "risk_tier": "HIGH"},
    "C-105": {"auto_renewal": True,  "change_of_control": True,  "liability_cap_usd": None,
              "governing_law": "Texas",             "risk_tier": "HIGH"},
    "C-106": {"auto_renewal": True,  "change_of_control": False, "liability_cap_usd": None,
              "governing_law": "Illinois",          "risk_tier": "MEDIUM"},
}

print(f"Loaded {len(CONTRACTS)} contracts with gold labels")

# %% [markdown]
# **The diligence playbook.** Every reviewer — human or model — works from the same standards
# document: field definitions, the risk rubric, a clause library, negotiation precedent. It's
# long, it's authoritative, and it is **byte-identical on every call**. Remember that phrase.

# %%
PLAYBOOK_CORE = """You are ClauseScan, the contract-diligence reviewer for Project HELVETICA \
(Aldgate Capital Partners' acquisition of Volta Industrial Group). You review supplier \
contracts and report five audited fields. Work only from the contract text provided. Never \
guess: if the contract is silent on a point, report it as absent rather than inventing terms.

## Audited fields
1. auto_renewal (boolean) — true only if the contract renews automatically absent notice. A \
fixed term that expires, or renewal "by mutual written agreement", is NOT auto-renewal.
2. change_of_control (boolean) — true if assignment or change of control of either party \
requires the counterparty's prior consent, or gives the counterparty a termination right. \
Free assignment or notice-only assignment is false.
3. liability_cap_usd (number or null) — the aggregate liability cap in USD. null if no cap is \
stated, if liability is expressly uncapped, or if an amendment removed the cap. Amendments \
override the original clause — always check for amendments before reporting.
4. governing_law (string) — the governing jurisdiction, e.g. "Delaware" or "England and Wales".
5. risk_tier (LOW | MEDIUM | HIGH) — apply this rule exactly:
   HIGH = change-of-control consent/termination right present AND no liability cap.
   MEDIUM = exactly one of those two red flags present.
   LOW = neither red flag (no change-of-control restriction AND a cap is stated).

## Review discipline
- Read every section, then re-check the two red-flag clauses before concluding.
- Quote-check: the evidence you cite must appear in the contract.
- Deal context: Aldgate's purchase will itself trigger change-of-control clauses across the \
estate — that is why the field is audited.
"""

# The clause library and precedent notes make the playbook realistically long. On a real
# engagement this is the 40-page standards PDF your firm maintains.
CLAUSE_LIBRARY = [
    ("Automatic renewal (evergreen)", "Term continues for successive periods unless a party gives notice by a stated deadline. Capture the notice window and the renewal period length.", "Notice windows under 30 days are operationally dangerous during an integration."),
    ("Fixed term with mutual-agreement renewal", "Agreement expires at the end of the stated term; any continuation needs a signed amendment.", "Often misread as auto-renewal. It is not."),
    ("Assignment with consent", "Neither party may assign without prior written consent; usually includes 'by operation of law' and merger language.", "This is a change-of-control restriction even when the words 'change of control' never appear."),
    ("Free assignment", "Either party may assign to affiliates or acquirers, sometimes with notice only.", "Notice-only assignment is NOT a change-of-control restriction."),
    ("Change-of-control termination right", "Counterparty may terminate upon a change of control of the other party.", "A termination right is as dangerous to deal value as a consent right — treat it as change_of_control = true."),
    ("Aggregate liability cap", "A single dollar ceiling on total liability under the agreement.", "Check whether carve-outs (confidentiality, IP, indemnity) sit outside the cap."),
    ("Capped at fees paid", "Liability limited to fees paid over a trailing window, sometimes with a dollar ceiling on top.", "Report the effective ceiling where one is stated; otherwise treat as a cap of unstated amount."),
    ("Uncapped liability", "No limitation-of-liability clause, or an express statement that liability is unlimited.", "Silence is a red flag, not a default. Report null."),
    ("Amendment overriding liability terms", "Later amendments may delete or rewrite the limitation-of-liability section.", "The amendment controls. Re-read amendments before reporting any cap."),
    ("Mutual indemnity, uncapped", "Each party indemnifies the other for defined claim classes with no ceiling.", "Uncapped indemnities alongside a deleted cap compound exposure."),
    ("Governing law", "The jurisdiction whose law governs the contract.", "Distinguish governing law from venue/forum; report the law."),
    ("Service levels and credits", "Uptime or performance commitments with credit remedies.", "Credits are usually the exclusive remedy — note but do not audit."),
    ("Payment terms", "Net-day windows, late interest, price-adjustment indices.", "Index-linked price escalators matter for the operating model, not this audit."),
    ("Confidentiality", "Mutual non-disclosure obligations with survival periods.", "Often carved out of the liability cap."),
    ("Insurance requirements", "Required coverage types and limits.", "Insurance limits are not liability caps. Do not conflate."),
    ("TUPE / employee transfer", "EU/UK staff-transfer regulations on outsourcing changes.", "Integration planning input; not an audited field."),
    ("Exclusivity", "Sole-supplier or minimum-purchase commitments.", "Flag in evidence notes if material; not an audited field."),
    ("Termination for convenience", "Either party may exit on notice without cause.", "Shortens effective exposure; note the notice window."),
    ("Most-favored-customer pricing", "Supplier promises pricing no worse than comparable customers.", "Diligence interest for procurement, not this audit."),
    ("IP indemnity", "Supplier defends infringement claims; sometimes uncapped.", "Carve-outs can sit outside the cap — read the cap clause's 'subject to' language."),
    ("Force majeure", "Excused performance on defined events.", "Not audited."),
    ("Audit rights", "Customer may audit supplier records or facilities.", "Not audited here; useful for integration."),
]

PRECEDENT_NOTES = [
    "Volta's 2024 acquisition of Kessler Tooling stalled five weeks on unflagged change-of-control consents across 1,100 supplier contracts.",
    "Aldgate's deal team treats any uncapped-liability supplier with consent rights as a Day-1 escalation.",
    "Notice windows: the integration office needs 90+ days of runway; sub-30-day windows go on the watch list.",
    "Caps stated in non-USD currencies are converted at the deal-model rate; report the stated figure and flag the currency in evidence.",
    "Where an MSA and SOW conflict, the MSA controls unless the SOW says otherwise — playbook v7, §3.2.",
    "Indemnity carve-outs outside the cap do not change liability_cap_usd; they belong in evidence.",
    "A termination-on-change-of-control right is reported as change_of_control = true even absent a consent requirement.",
    "Renewal 'by mutual written agreement' is not auto-renewal; the deal model treats those contracts as expiring.",
    "If two clauses conflict and no amendment resolves them, report the more conservative reading and say so in evidence.",
    "Evidence must cite section numbers; reviewers spot-check 10% of output against source text.",
]

WORKED_EXAMPLES = """
## Worked examples
Example A: a contract with free assignment and a $400,000 aggregate cap → change_of_control \
false, liability_cap_usd 400000, risk_tier LOW.
Example B: a contract requiring consent for change of control with a $1M cap → \
change_of_control true, cap 1000000, exactly one red flag → risk_tier MEDIUM.
Example C: a contract with a consent requirement whose cap was deleted by amendment → \
change_of_control true, liability_cap_usd null, both red flags → risk_tier HIGH.
"""


def _build_playbook() -> str:
    parts = [PLAYBOOK_CORE, "\n## Clause library\n"]
    for name, definition, red_flag in CLAUSE_LIBRARY:
        parts.append(f"### {name}\n{definition}\nRed flag note: {red_flag}\n")
    parts.append("\n## Negotiation precedent notes\n")
    for note in PRECEDENT_NOTES:
        parts.append(f"- {note}")
    parts.append(WORKED_EXAMPLES)
    text = "\n".join(parts)
    # Pad deterministically past the largest minimum cacheable prefix (4096 tokens on
    # Opus/Haiku; ~4 chars per token) so the caching lever works on every model.
    annex = "\n\n## Annex: clause library (deal-team annotated re-issue)\n" + "\n".join(
        f"### {n}\n{d}\nRed flag note: {r}\n" for n, d, r in CLAUSE_LIBRARY)
    while len(text) < 22000:
        text += annex
    return text


PLAYBOOK = _build_playbook()
print(f"Playbook built: {len(PLAYBOOK):,} chars (~{len(PLAYBOOK)//4:,} tokens) — identical on every call")

# %% [markdown]
# **The grader.** Five audited fields per contract, deterministic checks, gold prepared by
# hand. Accuracy is the **gate**: an optimization that breaks accuracy isn't an optimization,
# it's a liability transfer from your COGS line to your malpractice insurance.

# %%
def _normalize_cap(value):
    """Coerce model output for liability_cap_usd into a float or None."""
    if value is None:
        return None
    if isinstance(value, str):
        v = value.strip().lower().replace("$", "").replace(",", "")
        if v in ("", "none", "null", "n/a", "uncapped", "no cap"):
            return None
        try:
            return float(v)
        except ValueError:
            return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def grade_fields(fields: dict, gold: dict) -> dict:
    """Per-field pass/fail for one contract."""
    f = fields or {}
    checks = {}
    checks["auto_renewal"] = bool(f.get("auto_renewal")) == gold["auto_renewal"]
    checks["change_of_control"] = bool(f.get("change_of_control")) == gold["change_of_control"]
    cap = _normalize_cap(f.get("liability_cap_usd"))
    if gold["liability_cap_usd"] is None:
        checks["liability_cap_usd"] = cap is None
    else:
        checks["liability_cap_usd"] = cap is not None and abs(cap - gold["liability_cap_usd"]) < 1
    gl, gg = str(f.get("governing_law") or "").lower(), gold["governing_law"].lower()
    checks["governing_law"] = bool(gl) and (gg in gl or gl in gg)
    checks["risk_tier"] = str(f.get("risk_tier") or "").strip().upper() == gold["risk_tier"]
    return checks


def extract_json(text: str):
    """Pull the first valid JSON object out of free text (v0 needs this; v1 may not)."""
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        pass
    for start, ch in enumerate(text):
        if ch != "{":
            continue
        depth = 0
        for end in range(start, len(text)):
            if text[end] == "{":
                depth += 1
            elif text[end] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:end + 1])
                    except json.JSONDecodeError:
                        break
    return None


print("Grader ready — 5 audited fields × 6 contracts = 30 checks; gate is ≥ 90% (27/30)")

# %% [markdown]
# **ClauseScan v0 — exactly as inherited.** Read it the way you'd read a client's codebase on
# day one of a rescue: not to mock it, but to find where the money and the seconds go.

# %% [markdown]
# ### 🧭 Decision log — the truncation guard, and the measurement that motivated it
#
# `extract_json` returns `None` when it can't find JSON. A truncated response therefore scores
# **0/5 and looks exactly like a comprehension failure.** That is the worst possible failure mode for
# an accuracy gate: the number goes down, and the number gives you no way to find out why.
#
# I went looking for how a response could get truncated on a 5-field extraction with generous
# `max_tokens`, and found it. Measured 2026-08-28 on `anthropic` 1.1.0, contract-extraction prompt
# (easy) and a combinatorial puzzle (hard):
#
# | Model | `thinking` param | Prompt | `thinking` block? | out tokens | `stop_reason` |
# |---|---|---|---|---|---|
# | Sonnet 5 | omitted | easy | no | 605 | `end_turn` |
# | Sonnet 5 | `adaptive` (explicit) | easy | no | 699 | `end_turn` |
# | Opus 4.8 | omitted | easy | no | 577 | `end_turn` |
# | Opus 4.8 | `adaptive` (explicit) | easy | **yes** | 750 | `end_turn` |
# | Sonnet 5 | omitted | **hard** | **yes** | **800 (= cap)** | **`max_tokens`** |
# | Sonnet 5 | `adaptive` (explicit) | **hard** | **yes** | 4000 (= cap) | `max_tokens` |
# | Sonnet 5 | `effort="low"` | **hard** | **yes** | 4000 (= cap) | `max_tokens` |
#
# **What this actually shows** — and it is *not* what I expected going in:
#
# 1. **Adaptive thinking on Sonnet 5 is on by default, but it is genuinely adaptive.** With
#    `thinking` omitted it produced no thinking block at all on the easy prompt, and a large one on
#    the hard prompt. So there is no flat "thinking tax" to subtract from every call. I had assumed a
#    constant surcharge; the measurement says otherwise. Recording the correction rather than the
#    assumption.
# 2. **`effort="low"` did not suppress it.** The effort dial modulates *how much*, not *whether*, and
#    on the hard prompt it still ran to the cap. Do not reach for `effort` as a truncation fix.
# 3. **The real risk is variance, not average cost.** On routine contracts thinking costs nothing.
#    On the one contract that confuses the model, it can consume the entire `max_tokens` budget —
#    and the confirmed result is `stop_reason="max_tokens"` with **one `thinking` block and zero
#    `text` blocks**. No JSON. No answer. Silent 0/5.
#
# That is the coupling worth internalising: **the levers that cut cost (a cheaper model, a tighter
# `max_tokens`) are the same levers that make this failure more likely**, and it lands on the hardest
# contracts — the ones that decide whether you clear a 90% gate. So the guard goes in *before* the
# optimization sprint, not after.

# %%
class Truncated(RuntimeError):
    """Raised when a response stops on max_tokens instead of finishing.

    Carries the cost and call count already incurred, so a truncated run still shows up
    honestly in the cost column rather than looking free.
    """
    def __init__(self, message, cost=0.0, calls=0):
        super().__init__(message)
        self.cost, self.calls = cost, calls


def guard(response, model, *, cost=0.0, calls=0, where=""):
    """Fail loudly on truncation; return the response otherwise.

    The guard is behaviour-preserving on any run that was not already broken: if nothing
    truncates, every pipeline behaves exactly as before. All it removes is the ability to
    score a truncated response as a wrong answer.
    """
    if response.stop_reason == "max_tokens":
        kinds = [b.type for b in response.content]
        raise Truncated(
            f"stop_reason=max_tokens at {where or model} "
            f"(blocks={kinds}, out={response.usage.output_tokens}) — "
            f"raise max_tokens or lower thinking effort; do NOT grade this as a wrong answer",
            cost=cost, calls=calls)
    return response


print("Truncation guard armed — max_tokens stops now raise instead of scoring 0/5")

# %%
EXTRACT_INSTRUCTION = (
    "Determine the five audited fields for the contract below. Respond with a JSON object "
    "with keys auto_renewal, change_of_control, liability_cap_usd, governing_law, risk_tier, "
    "evidence.\n\nCONTRACT:\n"
)


def clausescan_v0(contract: dict) -> dict:
    """The pipeline the demo team shipped. Two passes, Opus for everything, no caching,
    verbose output, no streaming, sequential by construction."""
    t0 = time.perf_counter()
    calls = []

    # PASS 1 — "first, really understand the contract" (a full briefing nobody reads)
    r1 = client.messages.create(
        model=MODEL_OPUS, max_tokens=8000,
        system=PLAYBOOK,  # 5K+ tokens, re-billed at full price on every single call
        messages=[{"role": "user", "content":
                   "Write a detailed clause-by-clause briefing of this contract, with "
                   "commentary on anything unusual, before any extraction is attempted.\n\n"
                   + contract["text"]}],
    )
    calls.append((MODEL_OPUS, r1.usage))
    guard(r1, MODEL_OPUS, cost=sum(calculate_cost(m, u) for m, u in calls),
          calls=len(calls), where="v0 pass 1 (briefing)")

    # PASS 2 — extraction, with the briefing AND the contract AND the playbook again
    r2 = client.messages.create(
        model=MODEL_OPUS, max_tokens=8000,
        system=PLAYBOOK,
        messages=[{"role": "user", "content":
                   "Here is an internal briefing of a contract:\n\n" + text_of(r1)
                   + "\n\nNow, explain your reasoning step by step in detail, and then output "
                   + "a JSON object with keys auto_renewal, change_of_control, "
                   + "liability_cap_usd, governing_law, risk_tier, evidence.\n\nCONTRACT:\n"
                   + contract["text"]}],
    )
    calls.append((MODEL_OPUS, r2.usage))
    guard(r2, MODEL_OPUS, cost=sum(calculate_cost(m, u) for m, u in calls),
          calls=len(calls), where="v0 pass 2 (extraction)")

    return {
        "fields": extract_json(text_of(r2)),
        "calls": calls,
        "elapsed": time.perf_counter() - t0,
    }


print("ClauseScan v0 loaded (guarded — unchanged behaviour unless it truncates)")

# %%
def run_portfolio(pipeline, contracts=CONTRACTS, gold=GOLD, workers: int = 0,
                  warm_first: bool = True) -> dict:
    """Run a pipeline over the contract sample; grade, time, and price every contract."""

    def run_one(contract):
        try:
            out = pipeline(contract)
        except Truncated as e:
            # A truncated response is an INFRASTRUCTURE failure, not a comprehension failure.
            # Left unguarded it returns None fields and scores a silent 0/5 — indistinguishable
            # from the model simply being wrong, which is the single most misleading thing an
            # accuracy number can hide. Surface it and exclude it from the denominator.
            return {"id": contract["id"], "vendor": contract["vendor"], "error": str(e),
                    "fields": None, "checks": {}, "n_correct": 0, "n_fields": 0,
                    "elapsed": float("nan"), "cost": e.cost, "calls": e.calls}
        except Exception as e:  # transport / rate-limit / API errors
            return {"id": contract["id"], "vendor": contract["vendor"],
                    "error": f"{type(e).__name__}: {e}",
                    "fields": None, "checks": {}, "n_correct": 0, "n_fields": 0,
                    "elapsed": float("nan"), "cost": 0.0, "calls": 0}
        checks = grade_fields(out["fields"], gold[contract["id"]])
        return {
            "id": contract["id"], "vendor": contract["vendor"],
            "fields": out["fields"], "checks": checks, "error": None,
            "n_correct": sum(checks.values()), "n_fields": len(checks),
            "elapsed": out["elapsed"],
            "cost": sum(calculate_cost(m, u) for m, u in out["calls"]),
            "calls": len(out["calls"]),
        }

    wall0 = time.perf_counter()
    if workers and len(contracts) > 1:
        rows = []
        head = contracts[0]
        if warm_first:
            rows.append(run_one(head))  # one call writes the cache before the fan-out
            remaining = contracts[1:]
        else:
            remaining = contracts
        with ThreadPoolExecutor(max_workers=workers) as ex:
            rows.extend(ex.map(run_one, remaining))
    else:
        rows = [run_one(c) for c in contracts]
    wall = time.perf_counter() - wall0

    ok = [r for r in rows if not r.get("error")]
    errors = [r for r in rows if r.get("error")]
    total_fields = sum(r["n_fields"] for r in ok)
    if not ok:
        raise RuntimeError(f"every contract errored: {[r['error'] for r in errors]}")
    return {
        "rows": rows,
        # Denominator is graded fields only. An errored contract does NOT quietly count as
        # 5 wrong answers — it counts as a run that did not produce a gradeable result, and
        # it is reported separately so it cannot hide inside an accuracy percentage.
        "accuracy": sum(r["n_correct"] for r in ok) / total_fields,
        "n_errors": len(errors),
        "errors": [f"{r['id']}: {r['error']}" for r in errors],
        "p50_s": statistics.median(r["elapsed"] for r in ok),
        "cost_per_contract": sum(r["cost"] for r in rows) / len(rows),
        "total_cost": sum(r["cost"] for r in rows),
        "wall_s": wall,
    }


def print_report(report: dict, label: str):
    rows = [[r["id"], r["vendor"][:28],
             "ERROR" if r.get("error") else f"{r['n_correct']}/{r['n_fields']}",
             "—" if r.get("error") else f"{r['elapsed']:.1f}s",
             f"${r['cost']:.4f}", r["calls"]]
            for r in report["rows"]]
    print(f"\n── {label} " + "─" * max(1, 64 - len(label)))
    print(tabulate(rows, headers=["ID", "Vendor", "Fields", "TTC", "Cost", "Calls"],
                   tablefmt="simple"))
    print(f"\n  accuracy {report['accuracy']*100:.0f}%   ·   p50 {report['p50_s']:.1f}s/contract"
          f"   ·   ${report['cost_per_contract']:.4f}/contract"
          f"   ·   batch wall-clock {report['wall_s']:.0f}s")
    if report.get("n_errors"):
        print(f"  ⚠️  {report['n_errors']} contract(s) excluded from the accuracy denominator: "
              f"{report['errors']}")


print("Portfolio runner ready (hardened: truncation guard + errors excluded from the "
      "accuracy denominator). The baseline itself is measured and frozen two cells down.")

# %% [markdown]
# ### 🧭 Decision log — a gate you only pull once is a coin flip wearing a lab coat
#
# The shipped gate is **27/30 field checks on a single run**. Three things follow, and all three get
# worse as the sprint progresses:
#
# 1. **Resolution.** 30 checks means accuracy moves in **3.3-point steps**. There is no such thing as
#    "91%" here. The holdout is worse: 2 contracts × 5 fields = **10 checks**, so *one* miss is
#    exactly 90% and *two* is a fail. Any claim finer than that granularity is invented.
# 2. **No repeats.** One run cannot distinguish "this config is 93% accurate" from "this config got
#    lucky once." The API is not deterministic, and nothing here pins a seed.
# 3. **Variance rises exactly as the gate matters more.** Every lever in Part 4 — cheaper model, less
#    thinking, tighter `max_tokens` — *increases* run-to-run spread. So the instrument gets noisier at
#    precisely the moment the decision gets tighter.
#
# So: run each config **k times** and gate on the **conservative** statistic, carrying over
# `pass@k` / `pass^k` from the evals exercise.
#
# - **`pass@k`** — passed at least once in k runs. Optimistic. Right for "can it do this at all?"
# - **`pass^k`** — passed in **every** one of k runs. Pessimistic. Right for "will it hold at 3am on
#   contract 190,000 of 248,000?"
#
# A config whose *mean* accuracy is 93% but whose *min* is 87% has not cleared a 90% SLA — it has
# failed it intermittently. On a 248,000-document estate, "intermittently below spec" is roughly
# **32,000 documents** reviewed by a pipeline that was out of contract. That is the sentence that
# matters at the steering committee, and only the min tells you to say it.
#
# Practical budget note: run the ladder at **k=1–2** while turning dials, and **k=5** only on
# finalists. Spending k=5 on every rung buys precision about configs you're going to discard.

# %%
def run_portfolio_k(pipeline, k: int = 3, contracts=CONTRACTS, gold=GOLD, **kw) -> dict:
    """Run a config k times and report the distribution, not a single lucky number.

    Returns mean / min / max accuracy, per-field pass^k, and the medians of p50 and
    cost across runs. Gate on `acc_min` (or `passk_accuracy`), never on `acc_mean`.
    """
    runs = [run_portfolio(pipeline, contracts=contracts, gold=gold, **kw) for _ in range(k)]

    # pass^k per (contract, field): correct in EVERY run. Errored runs count as not-passed
    # for pass^k — an intermittent crash is an intermittent failure of the deliverable.
    always, ever, total = 0, 0, 0
    for c in contracts:
        cid = c["id"]
        per_run = []
        for r in runs:
            row = next((x for x in r["rows"] if x["id"] == cid), None)
            per_run.append(row["checks"] if row and not row.get("error") else {})
        for field in gold[cid]:
            total += 1
            got = [chk.get(field, False) for chk in per_run]
            always += all(got)
            ever += any(got)

    accs = [r["accuracy"] for r in runs]
    return {
        "k": k, "runs": runs,
        "acc_mean": statistics.mean(accs),
        "acc_min": min(accs),
        "acc_max": max(accs),
        "acc_stdev": statistics.stdev(accs) if k > 1 else 0.0,
        "passk_accuracy": always / total,   # pass^k — every run correct
        "pass_at_k": ever / total,          # pass@k — at least one run correct
        "p50_s": statistics.median(r["p50_s"] for r in runs),
        "cost_per_contract": statistics.median(r["cost_per_contract"] for r in runs),
        "total_cost": sum(r["total_cost"] for r in runs),
        "n_errors": sum(r["n_errors"] for r in runs),
        "n_checks": total,
    }


def print_k_report(rep: dict, label: str):
    print(f"\n── {label}  (k={rep['k']}) " + "─" * max(1, 52 - len(label)))
    print(f"  accuracy   mean {rep['acc_mean']*100:5.1f}%   min {rep['acc_min']*100:5.1f}%   "
          f"max {rep['acc_max']*100:5.1f}%   sd {rep['acc_stdev']*100:.1f}pp")
    print(f"  pass^k     {rep['passk_accuracy']*100:5.1f}%  (correct in ALL {rep['k']} runs, "
          f"n={rep['n_checks']} field-checks)")
    print(f"  pass@k     {rep['pass_at_k']*100:5.1f}%  (correct in at least one run)")
    print(f"  p50 {rep['p50_s']:.1f}s/contract   ·   ${rep['cost_per_contract']:.4f}/contract"
          f"   ·   ${rep['total_cost']:.4f} spent over {rep['k']} runs")
    if rep["n_errors"]:
        print(f"  ⚠️  {rep['n_errors']} errored contract-run(s) across the k runs")
    # Gate on the conservative statistic. This is the whole point of the cell.
    verdict = "PASS" if rep["acc_min"] >= 0.90 else "FAIL"
    print(f"  GATE (min accuracy ≥ 90%): {verdict}"
          + ("" if verdict == "PASS" else
             f"   ← mean of {rep['acc_mean']*100:.1f}% would have hidden this"))


print("k-repeat harness ready — gate on acc_min / pass^k, never on acc_mean")

# %% [markdown]
# ### 🧭 Decision log — freeze the baseline, or every score you quote is unanchored
#
# `BASELINE` shipped as a module-level global, measured fresh on every **Run All**. Every leaderboard
# number in this notebook is a *ratio* against it:
#
# `SCORE = 50 × (baseline $ / your $) + 50 × (baseline p50 / your p50)`
#
# So if you re-run tomorrow, v0 is re-measured against a different day's API latency and queue depth,
# and **every historical score silently re-anchors**. Your "412" becomes a "389" with no code change,
# and you have no way to tell whether you regressed or the denominator moved. Worse, it moves in the
# direction that flatters you: a slow baseline day inflates every optimized score.
#
# Fix: measure v0 **once**, write it to disk with a fingerprint, and load it thereafter. Same instinct
# as the sha-frozen corpus in `01_evals` — **a baseline you can't reproduce isn't a baseline, it's an
# anecdote.**
#
# The fingerprint records everything that would invalidate the comparison: git sha, model IDs, the
# (now corrected) pricing table, a hash of the contract set, the SDK version, and the timestamp. If
# any of those change, the stored baseline is no longer comparable and the cell says so out loud
# rather than quietly serving a stale number.
#
# The JSON is gitignored — the executed cell output is the evidence, matching the `eval_results/`
# precedent from `01_evals`. The fingerprint is printed, so it survives in the committed notebook.

# %%
BASELINE_PATH = pathlib.Path("baseline_v0.json")


def _fingerprint() -> dict:
    """Everything that, if it changed, would make a stored baseline incomparable."""
    try:
        sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, timeout=10).stdout.strip() or "unknown"
    except Exception:
        sha = "unknown"
    corpus = hashlib.sha256(
        json.dumps([{"id": c["id"], "text": c["text"]} for c in CONTRACTS],
                   sort_keys=True).encode()
    ).hexdigest()[:12]
    return {
        "git_sha": sha,
        "corpus_sha256_12": corpus,
        "models": {"haiku": MODEL_HAIKU, "sonnet": MODEL_SONNET, "opus": MODEL_OPUS},
        "pricing": {k: v for k, v in PRICING.items()},
        "pricing_verified_on": PRICING_VERIFIED_ON,
        "anthropic_sdk": anthropic.__version__,
        "pipeline": "clausescan_v0",
    }


def _slim(report: dict) -> dict:
    """Persistable view of a report — drop raw model output, keep the numbers and the grades."""
    return {
        "accuracy": report["accuracy"], "p50_s": report["p50_s"],
        "cost_per_contract": report["cost_per_contract"],
        "total_cost": report["total_cost"], "wall_s": report["wall_s"],
        "n_errors": report.get("n_errors", 0), "errors": report.get("errors", []),
        "rows": [{"id": r["id"], "vendor": r["vendor"], "n_correct": r["n_correct"],
                  "n_fields": r["n_fields"], "elapsed": r["elapsed"], "cost": r["cost"],
                  "calls": r["calls"], "checks": r.get("checks", {}),
                  "error": r.get("error")} for r in report["rows"]],
    }


fp_now = _fingerprint()

if BASELINE_PATH.exists():
    stored = json.loads(BASELINE_PATH.read_text())
    BASELINE = stored["report"]
    drift = {k: (stored["fingerprint"].get(k), fp_now.get(k))
             for k in fp_now if stored["fingerprint"].get(k) != fp_now.get(k)}
    print(f"Loaded frozen baseline from {BASELINE_PATH} "
          f"(measured {stored['measured_at_utc']})")
    if drift:
        print("  ⚠️  FINGERPRINT DRIFT — the stored baseline may not be comparable:")
        for k, (was, now) in drift.items():
            print(f"      {k}: stored={was!r}  now={now!r}")
        print("      Delete baseline_v0.json to re-measure if this matters.")
else:
    print("No frozen baseline found. Running ClauseScan v0 on the six-contract sample "
          "(sequential, ~2-4 minutes — the slowness IS the data)...")
    report = run_portfolio(clausescan_v0)
    BASELINE = _slim(report)
    BASELINE_PATH.write_text(json.dumps(
        {"measured_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
         "fingerprint": fp_now, "report": BASELINE}, indent=2))
    print(f"Measured and froze baseline → {BASELINE_PATH}")

print_report(BASELINE, "ClauseScan v0 — the inherited baseline (FROZEN)")

sla_p50 = "PASS" if BASELINE["p50_s"] <= 5 else "FAIL"
print(f"\n  SLA check: p50 ≤ 5s → {sla_p50}   ·   accuracy ≥ 90% → "
      f"{'PASS' if BASELINE['accuracy'] >= 0.9 else 'FAIL'}"
      f"   ·   COGS ≤ $0.02 → "
      f"{'PASS' if BASELINE['cost_per_contract'] <= 0.02 else 'FAIL'}")
print("\n  Baseline fingerprint (this is what every score below is anchored to):")
for k, v in fp_now.items():
    print(f"    {k}: {v}")


# ── The same discipline, generalised ──────────────────────────────────
# Every expensive block below (the ablation ladder, the router eval, the k=5 finalists) is
# a *measurement*, not a computation: re-running it re-bills the API AND re-rolls the dice.
# Memoize each one behind the same fingerprint as the baseline, so "Run All" stays
# idempotent and cheap — and so a number quoted in the writeup is the number that was
# actually measured, not a fresh sample that happens to look similar.
RESULTS_DIR = pathlib.Path("run_cache")


def freeze_result(name: str, compute, force: bool = False):
    """Measure once, record with a fingerprint, reload thereafter."""
    RESULTS_DIR.mkdir(exist_ok=True)
    path = RESULTS_DIR / f"{name}.json"
    if path.exists() and not force:
        blob = json.loads(path.read_text())
        drift = [k for k, v in _fingerprint().items() if blob["fingerprint"].get(k) != v]
        print(f"\u21ba loaded '{name}' from {path}  (measured {blob['measured_at_utc']})")
        if drift:
            print(f"   \u26a0\ufe0f  fingerprint drift on {drift} \u2014 delete {path} to re-measure")
        return blob["result"]
    result = compute()
    path.write_text(json.dumps(
        {"measured_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
         "fingerprint": _fingerprint(), "result": result}, indent=2))
    print(f"\u2713 measured and froze '{name}' \u2192 {path}")
    return result

# %% [markdown]
# **The diagnosis.** v0 is *correct* — and that's exactly what makes it dangerous: nothing
# looks broken in the demo, so it ships, and the waste compounds 248,000 times. Name the sins:
#
# 1. **Opus for everything.** Most of a contract estate is boilerplate a cheaper model reads
#    perfectly well.
# 2. **The 5K-token playbook is re-billed at full price on every call** — twice per contract.
#    It never changes. It's the textbook prompt-caching candidate.
# 3. **Two serial round trips** where one would do. The "briefing" pass doubles latency and
#    bills its own output as the next call's input.
# 4. **Verbose free-text output** — "explain step by step in detail, then JSON." Output tokens
#    are **5× the price** of input tokens, and every extra token also costs wall-clock time.
# 5. **No streaming.** The analyst stares at a dead screen for the full TTC.
# 6. **Sequential batch.** Six contracts take six contracts' worth of wall-clock; 248,000 take
#    a quarter-million.
#
# Five of these are levers you pull to make the surviving calls cheaper or faster. The sixth,
# the round trip, isn't — it's a call you delete before you optimize the rest. Part 4 takes
# them one at a time, with the meter running.

# %% [markdown]
# # Part 4 · The six levers
#
# **First, eliminate the round trip you never needed.** Before you make any call cheaper, delete
# the one you didn't need. Every serial round trip pays full freight: network + prefill +
# generation, and chained calls re-bill earlier output as new input. v0's "briefing" pass is
# pure round-trip tax — the same tax you'll meet again with tool calls (request → `tool_use` →
# execute → result → response) and multi-step chains. Collapse steps that don't earn their
# latency. v0's two passes become one, and every lever below operates on that single surviving
# call.
#
# ## Lever 1 — Prompt caching: stop re-buying the playbook
#
# One call per contract now. Make it stop re-buying the playbook.
#
# Caching is a **prefix match**: tools → system → messages, and any byte change invalidates
# everything after it. Our playbook is a frozen prefix by design. Mark it with `cache_control`
# and the API stores the processed prefix: the first call pays a **1.25×** write premium;
# every call inside the TTL (5 min) reads it back at **0.1×** — and skips reprocessing it,
# which also cuts TTFT.
#
# Field notes for client work: minimum cacheable prefix is **4096 tokens on Opus/Haiku, 2048
# on Sonnet** (shorter prefixes silently don't cache); keep volatile content — timestamps,
# request IDs, the contract itself — *after* the cached block; and verify with
# `usage.cache_read_input_tokens`, not vibes.

# %%
CACHED_SYSTEM = [{"type": "text", "text": PLAYBOOK, "cache_control": {"type": "ephemeral"}}]


def cached_extract(contract, model=MODEL_SONNET):
    t0 = time.perf_counter()
    resp = client.messages.create(
        model=model, max_tokens=1200, system=CACHED_SYSTEM,
        messages=[{"role": "user", "content": EXTRACT_INSTRUCTION + contract["text"]}],
    )
    return resp, time.perf_counter() - t0


for label in ("COLD (writes cache)", "WARM (reads cache)"):
    resp, secs = cached_extract(CONTRACTS[0])
    u = resp.usage
    print(f"{label:20s} {secs*1000:6.0f}ms · uncached_in={u.input_tokens:>5} · "
          f"cache_write={u.cache_creation_input_tokens or 0:>5} · "
          f"cache_read={u.cache_read_input_tokens or 0:>5} · "
          f"cost=${calculate_cost(MODEL_SONNET, u):.5f}")

print("\nWatch cache_read jump from 0 to ~the playbook size, and cost drop with it.")

# %% [markdown]
# ## Lever 2 — Model routing: a portfolio, not a model
#
# On a 248K-contract estate, maybe 15–25% genuinely need senior attention — amendments that
# rewrite terms, conflicting clauses, unusual indemnities. The rest is boilerplate. So run a
# **triage pass on Haiku** (cheap, fast, tiny output) and route: ROUTINE → Haiku or Sonnet,
# COMPLEX → Sonnet or Opus. This is staffing leverage, the thing your firm already understands:
# you don't put the senior partner on every NDA.

# %%
TRIAGE_SYSTEM = (
    "You triage supplier contracts for a diligence pipeline. Classify the contract as "
    "COMPLEX if it contains any of: an amendment that modifies earlier terms, clauses that "
    "conflict with each other, unusual or uncapped indemnities, or liability terms that are "
    "ambiguous or implied rather than stated. Otherwise classify it as ROUTINE."
)

TRIAGE_SCHEMA = {
    "type": "json_schema",
    "schema": {
        "type": "object",
        "properties": {
            "complexity": {"type": "string", "enum": ["ROUTINE", "COMPLEX"]},
            "reason": {"type": "string", "description": "One short sentence."},
        },
        "required": ["complexity", "reason"],
        "additionalProperties": False,
    },
}


def triage(contract):
    resp = client.messages.create(
        model=MODEL_HAIKU, max_tokens=150, system=TRIAGE_SYSTEM,
        messages=[{"role": "user", "content": contract["text"]}],
        output_config={"format": TRIAGE_SCHEMA},
    )
    guard(resp, MODEL_HAIKU, calls=1, where="triage")
    verdict = extract_json(text_of(resp)) or {}
    return verdict.get("complexity", "COMPLEX"), resp  # fail safe: unknown → COMPLEX


rows = []
for c in CONTRACTS:
    verdict, resp = triage(c)
    rows.append([c["id"], c["vendor"][:28], verdict,
                 f"${calculate_cost(MODEL_HAIKU, resp.usage):.5f}"])
print(tabulate(rows, headers=["ID", "Vendor", "Triage", "Triage cost"], tablefmt="simple"))
print("\nA few tenths of a cent buys the routing decision. The savings come from what it "
      "routes AWAY from Opus.")

# %% [markdown]
# ## Lever 3 — Output discipline: schemas, not essays
#
# Output tokens cost **5× input tokens** and each one costs generation time (TTC ≈ TTFT +
# output_tokens ÷ OTPS). v0 asks for step-by-step prose *plus* JSON. The fix is **structured
# outputs**: `output_config.format` with a JSON schema. The response *is* the deliverable —
# valid JSON, no parsing regex, no preamble — and the risk rubric lives in the field
# descriptions, so the schema doubles as the spec your QA partner signs off on.
#
# Two dials that live in the same neighborhood:
# - `max_tokens` — a hard ceiling; right-size it to the artifact (our JSON needs ~300, not 8000).
# - `output_config.effort` — Sonnet/Opus accept `low`–`max` to trade depth for tokens.
#   **It errors on Haiku 4.5** — Haiku is already the low-latency tier.

# %%
EXTRACTION_SCHEMA = {
    "type": "json_schema",
    "schema": {
        "type": "object",
        "properties": {
            "auto_renewal": {"type": "boolean",
                             "description": "True only if the contract renews automatically absent notice."},
            "change_of_control": {"type": "boolean",
                                  "description": "True if assignment/change of control requires consent or grants a termination right."},
            "liability_cap_usd": {"anyOf": [{"type": "number"}, {"type": "null"}],
                                  "description": "Aggregate cap in USD. null if no cap is stated or an amendment removed it."},
            "governing_law": {"type": "string",
                              "description": "Jurisdiction, e.g. 'Delaware' or 'England and Wales'."},
            "risk_tier": {"type": "string", "enum": ["LOW", "MEDIUM", "HIGH"],
                          "description": "HIGH = change-of-control restriction AND no cap. MEDIUM = exactly one red flag. LOW = neither."},
            "evidence": {"type": "string",
                         "description": "One sentence citing the section numbers relied on."},
        },
        "required": ["auto_renewal", "change_of_control", "liability_cap_usd",
                     "governing_law", "risk_tier", "evidence"],
        "additionalProperties": False,
    },
}


def extract_structured(contract, model=MODEL_SONNET, cached=True, effort=None):
    """One call, schema-constrained output, optional cached playbook and effort dial."""
    t0 = time.perf_counter()
    output_config = {"format": EXTRACTION_SCHEMA}
    if effort and model != MODEL_HAIKU:   # effort errors on Haiku 4.5
        output_config["effort"] = effort
    resp = client.messages.create(
        model=model, max_tokens=1000,
        system=CACHED_SYSTEM if cached else PLAYBOOK,
        messages=[{"role": "user", "content": EXTRACT_INSTRUCTION + contract["text"]}],
        output_config=output_config,
    )
    guard(resp, model, cost=calculate_cost(model, resp.usage), calls=1,
          where=f"extract_structured({model}, effort={effort})")
    return {"fields": extract_json(text_of(resp)), "calls": [(model, resp.usage)],
            "elapsed": time.perf_counter() - t0}


demo = extract_structured(CONTRACTS[0], model=MODEL_SONNET, effort="low")
model_used, u = demo["calls"][0]
v0_row = BASELINE["rows"][0]
print(f"Structured single pass: {demo['elapsed']:.1f}s · {u.output_tokens} output tokens · "
      f"${calculate_cost(model_used, u):.5f}")
print(f"v0 on the same contract: {v0_row['elapsed']:.1f}s · ${v0_row['cost']:.4f} across "
      f"{v0_row['calls']} calls — most of it prose nobody reads.")
print(json.dumps(demo["fields"], indent=2))

# %% [markdown]
# ## Lever 4 — Streaming: TTFT is the UX number
#
# Stream the one surviving call. TTC is an economics number; **TTFT is a UX number** — it's the
# difference between an analyst who watches the answer assemble and one who alt-tabs to email.

# %%
t0 = time.perf_counter()
ttft, total, sresp = _stream_request(
    [{"role": "user", "content": EXTRACT_INSTRUCTION + CONTRACTS[0]["text"]}],
    model=MODEL_SONNET, max_tokens=1000, system=CACHED_SYSTEM,
    output_config={"format": EXTRACTION_SCHEMA},
)
print(f"Streamed single pass:  TTFT {ttft*1000:.0f}ms · TTC {total*1000:.0f}ms")
print(f"v0 two-pass TTC on this contract was {BASELINE['rows'][0]['elapsed']:.1f}s — "
      f"and its TTFT *was* its TTC, because nothing streamed.")

# %% [markdown]
# ## Lever 5 — Parallelize the portfolio (and warm the cache first)
#
# Contracts are independent; review them concurrently. One subtlety the pros get right: a
# cache entry only becomes readable once the first response **begins streaming** — N identical
# requests fired simultaneously all pay the cold price. So: **send one contract first to warm
# the cache, then fan out.** (`run_portfolio` has done this for you all along: `warm_first=True`.)
#
# Field notes: respect your rate-limit tier; the SDK retries 429s with backoff automatically,
# but a fan-out sized to your TPM limit is cheaper than a fan-out that thrashes retries.

# %%
seq = run_portfolio(lambda c: extract_structured(c, model=MODEL_SONNET), workers=0)
par = run_portfolio(lambda c: extract_structured(c, model=MODEL_SONNET), workers=4)
print(f"Sequential portfolio wall-clock: {seq['wall_s']:.0f}s")
print(f"Parallel (warm-first, 4 workers): {par['wall_s']:.0f}s")
print(f"Accuracy held at {par['accuracy']*100:.0f}% — speed that costs accuracy is not speed.")

# %% [markdown]
# ## Lever 6 — Two-speed architecture: the Batch API lane
#
# Not every contract needs an analyst watching. Split the workload like an engagement team
# would:
#
# - **Interactive lane** — working sessions, streamed, p50 ≤ 5s, everything you just built.
# - **Backfill lane** — the other ~235K documents run overnight through the **Batch API** at
#   **50% off all token charges** (most batches finish within an hour; results persist 29 days).
#
# Same models, same prompts, same schema — half the price, in exchange for latency you weren't
# going to spend anyway. This split — *what the client touches* vs *what runs in the dark* —
# is the single highest-leverage architecture question on volume engagements.

# %%
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from anthropic.types.messages.batch_create_params import Request


def build_batch_requests(contracts):
    return [
        Request(
            custom_id=c["id"],
            params=MessageCreateParamsNonStreaming(
                model=MODEL_SONNET, max_tokens=1000,
                system=CACHED_SYSTEM,  # caching works in batches too (best-effort hits)
                messages=[{"role": "user", "content": EXTRACT_INSTRUCTION + c["text"]}],
                output_config={"format": EXTRACTION_SCHEMA},
            ),
        )
        for c in contracts
    ]


batch_requests = build_batch_requests(CONTRACTS)
print(f"Built {len(batch_requests)} batch requests "
      f"(limits: 100K requests / 256 MB per batch)")

interactive_cpc = par["cost_per_contract"]
print(f"\nInteractive lane:  ${interactive_cpc:.4f}/contract")
print(f"Batch lane (50%):  ${interactive_cpc * BATCH_DISCOUNT:.4f}/contract")

RUN_BATCH = False  # flip to True to actually submit; poll with client.messages.batches.retrieve()
if RUN_BATCH and PROVIDER == "bedrock":
    # The Batch API is an Anthropic API endpoint — Amazon Bedrock doesn't expose it.
    # Everything above still stands: the requests are built, and the 50%-off economics
    # below are the actual teaching point. Bedrock has its own batch-inference product
    # with a different API; the lever (defer non-urgent work, pay less) is the same.
    print("⚠️  Skipping live submission: the Batch API isn't available on Amazon Bedrock.")
    print("    The batch requests above are built and valid — inspect them, and read the")
    print("    cost model below. On Bedrock you'd use its own batch-inference offering.")
elif RUN_BATCH:
    batch = client.messages.batches.create(requests=batch_requests)
    print(f"Submitted batch {batch.id} — status: {batch.processing_status}")
    print("Poll: client.messages.batches.retrieve(batch.id); "
          "results: client.messages.batches.results(batch.id)")

# %% [markdown]
# # Part 5 · The optimization sprint 🏁
#
# Now it's yours. `clausescan_v1` below starts as a faithful copy of v0 — it will score
# **≈ 100** (that's the baseline index). Pull levers by editing `CONFIG`, then re-run the
# scorecard. When `CONFIG` stops being enough, edit the function itself — that's encouraged.
#
# **Scoring** (the SLA, condensed into one number):
#
# ```
# accuracy < 90%  →  SCORE = 0 (gate — no partial credit for fast, wrong answers)
# otherwise       →  SCORE = 50 × (baseline_$ / your_$) + 50 × (baseline_p50 / your_p50)
# ```
#
# v0 = 100. Cut cost 4× and latency 2× → 300. A strong configuration clears 400.
#
# **Rules of engagement:**
# 1. Accuracy ≥ 90% on the sample — and your pipeline must *read the contracts*. Hardcoding
#    answers scores zero with the holdout set (and your code gets read out loud).
# 2. Any model, any caching, any routing, any concurrency. Creativity within the API is the point.
# 3. Post your leaderboard line in the session chat after each run. Beat the table.

# %% [markdown]
# ### ✏️ YOUR TURN — this CONFIG is your steering wheel
#
# Edit it HERE, re-run, watch the score.

# %%
# ✏️ YOUR TURN: edit this CONFIG
CONFIG = {
    # Lever 2 — routing. Try: triage_routing=True, routine_model=MODEL_HAIKU,
    #                         complex_model=MODEL_SONNET
    "triage_routing": False,
    "routine_model": MODEL_OPUS,
    "complex_model": MODEL_OPUS,

    # Lever 1 — cache the playbook prefix
    "cache_playbook": False,

    # Lever 3 + the round-trip collapse — one schema pass instead of briefing → essay → JSON
    "structured_single_pass": False,
    "max_tokens": 8000,           # right-size once output is disciplined (~1000)
    "effort": None,               # "low" | "medium" | "high" — Sonnet/Opus only

    # Lever 5 — portfolio concurrency (0 = sequential; warm-first is automatic)
    "parallel_workers": 0,

    # ── Beyond the notebook's six levers (see the decision log below) ──────────
    "derive_risk_tier": False,   # compute risk_tier in Python; stop paying a model for an `if`
    "drop_evidence": False,      # raises the score, deletes the audit trail. Read the log first.
}


def derive_risk_tier(fields: dict) -> str:
    """The playbook's own rule, executed rather than inferred.

    HIGH = change-of-control restriction AND no cap · MEDIUM = exactly one · LOW = neither.
    Both inputs are already extracted, so asking the model for this field buys nothing and
    costs three things: output tokens, latency, and a failure mode that can disagree with the
    very fields it is derived from (a HIGH tier next to a stated $250K cap).
    """
    flags = (int(bool(fields.get("change_of_control")))
             + int(_normalize_cap(fields.get("liability_cap_usd")) is None))
    return {2: "HIGH", 1: "MEDIUM", 0: "LOW"}[flags]


def extraction_schema(derive_tier: bool = False, drop_evidence: bool = False) -> dict:
    """EXTRACTION_SCHEMA with fields removed — the schema IS the output contract, so the
    cheapest way to stop paying for a field is to stop asking for it."""
    props = dict(EXTRACTION_SCHEMA["schema"]["properties"])
    if derive_tier:
        props.pop("risk_tier", None)
    if drop_evidence:
        props.pop("evidence", None)
    return {"type": "json_schema",
            "schema": {"type": "object", "properties": props,
                       "required": list(props), "additionalProperties": False}}


def clausescan_v1(contract: dict) -> dict:
    """Your pipeline. Starts as v0; ends wherever you take it."""
    t0 = time.perf_counter()
    calls = []

    # Routing (Lever 2)
    model = CONFIG["routine_model"]
    if CONFIG["triage_routing"]:
        verdict, tri_resp = triage(contract)
        calls.append((MODEL_HAIKU, tri_resp.usage))
        model = CONFIG["complex_model"] if verdict == "COMPLEX" else CONFIG["routine_model"]

    # Cached vs raw playbook (Lever 1)
    system = CACHED_SYSTEM if CONFIG["cache_playbook"] else PLAYBOOK

    if CONFIG["structured_single_pass"]:
        # Levers 3 + 4 — one pass, schema output, optional effort dial
        output_config = {"format": extraction_schema(CONFIG["derive_risk_tier"],
                                                     CONFIG["drop_evidence"])}
        if CONFIG["effort"] and model != MODEL_HAIKU:
            output_config["effort"] = CONFIG["effort"]
        resp = client.messages.create(
            model=model, max_tokens=CONFIG["max_tokens"], system=system,
            messages=[{"role": "user", "content": EXTRACT_INSTRUCTION + contract["text"]}],
            output_config=output_config,
        )
        calls.append((model, resp.usage))
        guard(resp, model, cost=sum(calculate_cost(m, u) for m, u in calls),
              calls=len(calls), where=f"v1 single pass ({model})")
        fields = extract_json(text_of(resp))
        if CONFIG["derive_risk_tier"] and fields:
            fields["risk_tier"] = derive_risk_tier(fields)
    else:
        # v0's two-pass flow, verbatim
        r1 = client.messages.create(
            model=model, max_tokens=CONFIG["max_tokens"], system=system,
            messages=[{"role": "user", "content":
                       "Write a detailed clause-by-clause briefing of this contract, with "
                       "commentary on anything unusual, before any extraction is attempted.\n\n"
                       + contract["text"]}],
        )
        calls.append((model, r1.usage))
        guard(r1, model, cost=sum(calculate_cost(m, u) for m, u in calls),
              calls=len(calls), where="v1 pass 1")
        r2 = client.messages.create(
            model=model, max_tokens=CONFIG["max_tokens"], system=system,
            messages=[{"role": "user", "content":
                       "Here is an internal briefing of a contract:\n\n" + text_of(r1)
                       + "\n\nNow, explain your reasoning step by step in detail, and then "
                       + "output a JSON object with keys auto_renewal, change_of_control, "
                       + "liability_cap_usd, governing_law, risk_tier, evidence.\n\nCONTRACT:\n"
                       + contract["text"]}],
        )
        calls.append((model, r2.usage))
        guard(r2, model, cost=sum(calculate_cost(m, u) for m, u in calls),
              calls=len(calls), where="v1 pass 2")
        fields = extract_json(text_of(r2))

    return {"fields": fields, "calls": calls, "elapsed": time.perf_counter() - t0}


def engagement_score(report: dict, baseline: dict) -> int:
    if report["accuracy"] < 0.90:
        return 0
    return round(50 * baseline["cost_per_contract"] / max(report["cost_per_contract"], 1e-9)
                 + 50 * baseline["p50_s"] / max(report["p50_s"], 1e-9))


def run_scorecard(pipeline, label="clausescan_v1", contracts=CONTRACTS, gold=GOLD):
    report = run_portfolio(pipeline, contracts=contracts, gold=gold,
                           workers=CONFIG.get("parallel_workers", 0))
    print_report(report, label)
    score = engagement_score(report, BASELINE)
    levers = []
    if CONFIG.get("triage_routing"):
        levers.append("routing")
    if CONFIG.get("cache_playbook"):
        levers.append("cache")
    if CONFIG.get("structured_single_pass"):
        levers.append("schema-1pass")
    if CONFIG.get("effort"):
        levers.append(f"effort-{CONFIG['effort']}")
    if CONFIG.get("parallel_workers"):
        levers.append(f"parallel-x{CONFIG['parallel_workers']}")
    if CONFIG.get("derive_risk_tier"):
        levers.append("derived-tier")
    if CONFIG.get("drop_evidence"):
        levers.append("no-evidence")
    gate = "" if report["accuracy"] >= 0.90 else "  ⛔ accuracy gate failed — score zeroed"
    print(f"\n  ENGAGEMENT SCORE: {score}  (v0 baseline = 100){gate}")
    print(f"  📋 leaderboard line →  SCORE {score} · acc {report['accuracy']*100:.0f}% · "
          f"p50 {report['p50_s']:.1f}s · ${report['cost_per_contract']:.4f}/contract · "
          f"levers: {'+'.join(levers) if levers else 'none'}")
    return report


print("Sprint harness ready. Edit CONFIG above, then run the next cell. Repeat until proud.")

# %%
# Rung 0 of the ablation ladder below IS this measurement — an unmodified CONFIG is a v0
# clone. Running it here too would spend another ~$0.95 of Opus two-pass calls to learn a
# number we are about to measure anyway, so it is off by default. Flip it if you want the
# notebook's original single-shot flow.
RUN_UNTUNED_V1 = False

if RUN_UNTUNED_V1:
    my_report = run_scorecard(clausescan_v1)
else:
    print("Skipped — the untuned CONFIG is a v0 clone and is measured as rung 0 of the "
          "ablation ladder below.\nSet RUN_UNTUNED_V1 = True to run it here instead.")

# %% [markdown]
# ### 🧭 Decision log — an ablation ladder, not a before/after
#
# The notebook's suggested path turns several dials and re-runs the scorecard. That gives you a
# **bundled A/B**: v0 → v1, one big number, no idea which change bought it. In `01_evals` the same
# shortcut would have reported "57% → 86%" when the honest ladder showed **57 → 57 → 86 → 86** — two
# of the four changes bought exactly nothing, and the bundled number credited them anyway.
#
# So: a **ladder**. Each rung adds **exactly one variable** to the rung below it, scored the same way
# against the same frozen baseline.
#
# | Rung | Adds | Isolates |
# |---|---|---|
# | 0 | v0 verbatim | the anchor — must score ≈ 100 |
# | 1 | `structured_single_pass` | the **round-trip collapse** alone (`max_tokens` still 8000) |
# | 2 | `max_tokens` 8000 → 1000 | **output right-sizing** alone |
# | 3 | `cache_playbook` | **prefix caching** alone |
# | 4 | Opus → Sonnet | the **model swap** alone (watch accuracy) |
# | 5 | `effort="low"` | the **thinking dial** alone |
# | 6 | triage routing → Haiku/Sonnet | **routing** alone (highest accuracy risk) |
# | 7 | `derive_risk_tier` | doing an `if` **in code** instead of in a model |
# | 8 | `parallel_workers=4` | **concurrency** — expected null on score, see below |
#
# Two predictions worth writing down *before* running, so the ladder can falsify them:
#
# - **Rung 8 buys ~nothing on the score.** `engagement_score` reads `report["p50_s"]` — the *median
#   per-contract elapsed time* — not wall-clock. Concurrency moves wall-clock. It should move the
#   score by roughly zero, and may move it *down* under contention. Its real contribution is
#   `warm_first=True`, which is already on.
# - **Rung 5 may buy less than expected.** The measurement above showed `effort="low"` does not
#   suppress adaptive thinking, and these contracts are short enough that Sonnet mostly declines to
#   think anyway.
#
# A ladder that confirms every prediction taught you nothing. The interesting rungs are the null ones.
#
# Run at **k=2** where it's cheap and **k=1** on the two Opus-heavy rungs; finalists get **k=5** after.

# %%
from contextlib import contextmanager


@contextmanager
def with_config(**overrides):
    """Temporarily patch CONFIG. Restores on exit — so a failed rung can't silently
    contaminate every rung after it, which is the classic way an ablation ladder lies."""
    saved = dict(CONFIG)
    CONFIG.update(overrides)
    try:
        yield
    finally:
        CONFIG.clear()
        CONFIG.update(saved)


V0_CONFIG = {
    "triage_routing": False, "routine_model": MODEL_OPUS, "complex_model": MODEL_OPUS,
    "cache_playbook": False, "structured_single_pass": False, "max_tokens": 8000,
    "effort": None, "parallel_workers": 0, "derive_risk_tier": False, "drop_evidence": False,
}

# Each rung is (label, {the ONE thing that changes vs the rung above}, k)
RUNGS = [
    ("0 · v0 verbatim (anchor)",       {},                                                  1),
    ("1 · + collapse round trip",      {"structured_single_pass": True},                    1),
    ("2 · + right-size max_tokens",    {"max_tokens": 1000},                                2),
    ("3 · + cache the playbook",       {"cache_playbook": True},                            2),
    ("4 · + Sonnet instead of Opus",   {"routine_model": MODEL_SONNET,
                                        "complex_model": MODEL_SONNET},                     2),
    ("5 · + effort=low",               {"effort": "low"},                                   2),
    ("6 · + triage routing to Haiku",  {"triage_routing": True,
                                        "routine_model": MODEL_HAIKU},                      2),
    ("7 · + derive risk_tier in code", {"derive_risk_tier": True},                          2),
    ("8 · + parallel workers",         {"parallel_workers": 4},                             2),
]


def run_ladder(rungs=RUNGS, contracts=CONTRACTS, gold=GOLD):
    """Climb the ladder, accumulating one change per rung. Returns a row per rung."""
    cfg, results = dict(V0_CONFIG), []
    for label, delta, k in rungs:
        cfg.update(delta)
        with with_config(**cfg):
            rep = run_portfolio_k(clausescan_v1, k=k, contracts=contracts, gold=gold,
                                  workers=CONFIG["parallel_workers"])
        # Gate and score on the CONSERVATIVE statistic, not the mean.
        score = (0 if rep["acc_min"] < 0.90 else
                 round(50 * BASELINE["cost_per_contract"] / max(rep["cost_per_contract"], 1e-9)
                       + 50 * BASELINE["p50_s"] / max(rep["p50_s"], 1e-9)))
        wall = statistics.median(r["wall_s"] for r in rep["runs"])
        results.append({"rung": label, "k": k, "score": score,
                        "acc_mean": rep["acc_mean"], "acc_min": rep["acc_min"],
                        "passk": rep["passk_accuracy"], "p50": rep["p50_s"],
                        "wall": wall, "cost": rep["cost_per_contract"],
                        "spend": rep["total_cost"], "errors": rep["n_errors"],
                        "delta": ", ".join(f"{k2}={v}" for k2, v in delta.items()) or "—"})
        r = results[-1]
        print(f"{label:34s} k={k}  score {r['score']:>4}  "
              f"acc {r['acc_mean']*100:5.1f}% (min {r['acc_min']*100:5.1f}%)  "
              f"p50 {r['p50']:5.1f}s  wall {r['wall']:5.1f}s  ${r['cost']:.4f}/contract",
              flush=True)
    return results


def print_ladder(results):
    prev = None
    rows = []
    for r in results:
        d_score = "—" if prev is None else f"{r['score'] - prev['score']:+d}"
        d_cost = "—" if prev is None else f"{(r['cost']/prev['cost'] - 1)*100:+.0f}%"
        rows.append([r["rung"], r["k"], r["score"], d_score,
                     f"{r['acc_mean']*100:.1f}%", f"{r['acc_min']*100:.1f}%",
                     f"{r['passk']*100:.1f}%", f"{r['p50']:.1f}s", f"{r['wall']:.0f}s",
                     f"${r['cost']:.4f}", d_cost])
        prev = r
    print(tabulate(rows, headers=["Rung", "k", "Score", "Δscore", "acc mean", "acc min",
                                  "pass^k", "p50", "wall", "$/contract", "Δ$"],
                   tablefmt="simple"))
    print(f"\n  Total ladder spend: ${sum(r['spend'] for r in results):.2f}")
    print("  Score is gated on acc MIN, not acc mean — a rung with a 93% mean and an 87% min "
          "scores 0.")


print("Ablation ladder defined — 9 rungs, one variable each. Run the next cell.")

# %%
LADDER = freeze_result("ladder", run_ladder)
print()
print_ladder(LADDER)

# %% [markdown]
# ### 🧭 Decision log — grade the router, not just the pipeline
#
# `triage()` is an LLM making a decision that **gates accuracy for every contract downstream**, and
# nothing in the lab checks whether it is any good. That is the same blind spot `01_evals` closes by
# grading the grader: an unevaluated component in the critical path is an assumption wearing the
# costume of a measurement.
#
# Two things make this eval worth doing carefully:
#
# **1. The label is not obvious, so measure it two ways.** The tempting move is to hand-label which
# contracts "look hard" and score triage against that. But the router's actual job is not to identify
# hard contracts — it is to identify **contracts the cheap model would get wrong**. Those are different
# sets, and only the second one costs you the gate. So this cell reports both:
#
# - **Rubric labels** — my a-priori reading against `TRIAGE_SYSTEM`'s own stated criteria.
# - **Empirical labels** — measured. Run the extraction on Haiku k times; any contract Haiku does not
#   get perfectly right on *every* run is one that must be routed away from Haiku. This is the label
#   that actually matters, and it can only be obtained by running the thing.
#
# **2. The two error directions are not equal, so never report one accuracy number.**
#
# | Error | What happens | Cost |
# |---|---|---|
# | **False ROUTINE** | a hard contract goes to Haiku | wrong fields → **the accuracy gate**, and it is invisible until the gate fails |
# | **False COMPLEX** | an easy contract goes to Sonnet/Opus | a fraction of a cent |
#
# These differ by orders of magnitude, so a single "triage is 83% accurate" is close to meaningless.
# `triage()` already encodes this asymmetry — an unparseable verdict defaults to `COMPLEX`, failing
# toward accuracy and away from savings. Worth naming as a deliberate design decision rather than a
# default: **when the two errors are asymmetric, the fail-safe direction is part of the design, not an
# implementation detail.** The demo run earlier routed 4 of 6 to COMPLEX, which is exactly this bias
# showing up as money rather than as risk.

# %%
# Rubric labels — my a-priori read against TRIAGE_SYSTEM's own stated criteria.
# (An amendment modifying earlier terms · conflicting clauses · unusual or uncapped
#  indemnities · liability terms that are ambiguous or implied rather than stated.)
TRIAGE_RUBRIC_LABELS = {
    "C-101": "ROUTINE",   # plain MSA, cap stated plainly, assignment free
    "C-102": "ROUTINE",   # fixed term, cap stated plainly, assignment free
    "C-103": "COMPLEX",   # "greater of ... provided the cap shall in no event exceed" — self-conflicting
    "C-104": "COMPLEX",   # uncapped indemnities + cap absent by SILENCE, not by statement
    "C-105": "COMPLEX",   # Amendment No. 2 deletes the cap clause outright
    "C-106": "ROUTINE",   # no cap, but plainly STATED (all caps) — not ambiguous, just oddly placed
}

TRIAGE_K = 3   # triage is ~$0.0008/contract; repeating it is essentially free


def empirical_routing_labels(k: int = 3):
    """The label that actually matters: does the CHEAP model get this contract right, every time?

    A contract Haiku nails on all k runs is safely ROUTINE. Anything else must be COMPLEX,
    regardless of whether it 'looks' hard to a human.
    """
    with with_config(**{**V0_CONFIG, "structured_single_pass": True, "max_tokens": 1000,
                        "cache_playbook": True, "routine_model": MODEL_HAIKU,
                        "complex_model": MODEL_HAIKU, "derive_risk_tier": False}):
        rep = run_portfolio_k(clausescan_v1, k=k)
    labels, detail = {}, {}
    for c in CONTRACTS:
        per_run = []
        for r in rep["runs"]:
            row = next((x for x in r["rows"] if x["id"] == c["id"]), None)
            per_run.append(0 if not row or row.get("error") else row["n_correct"])
        labels[c["id"]] = "ROUTINE" if all(n == 5 for n in per_run) else "COMPLEX"
        detail[c["id"]] = per_run
    return labels, detail, rep


def eval_triage(k: int = TRIAGE_K):
    """Run triage k times per contract; report stability, accuracy vs both label sets,
    and — the part that matters — the DIRECTION of every error."""
    votes, costs = {}, 0.0
    for c in CONTRACTS:
        v = []
        for _ in range(k):
            verdict, resp = triage(c)
            v.append(verdict)
            costs += calculate_cost(MODEL_HAIKU, resp.usage)
        votes[c["id"]] = v
    return votes, costs


def _measure_router():
    print("Measuring the empirical routing labels (what Haiku actually gets right)...")
    emp, detail, _ = empirical_routing_labels(k=TRIAGE_K)
    print("Evaluating triage() itself...")
    votes, cost = eval_triage(k=TRIAGE_K)
    return {"empirical": emp, "detail": detail, "votes": votes, "cost": cost}


_router = freeze_result("router_eval", _measure_router)
EMP_LABELS, EMP_DETAIL = _router["empirical"], _router["detail"]
TRIAGE_VOTES, TRIAGE_COST = _router["votes"], _router["cost"]

rows, fn, fp = [], [], []
for c in CONTRACTS:
    cid = c["id"]
    v = TRIAGE_VOTES[cid]
    majority = max(set(v), key=v.count)
    stable = "yes" if len(set(v)) == 1 else f"NO {v}"
    rubric, emp = TRIAGE_RUBRIC_LABELS[cid], EMP_LABELS[cid]
    # Error direction is judged against the EMPIRICAL label — the one with teeth.
    if emp == "COMPLEX" and majority == "ROUTINE":
        direction = "⛔ FALSE ROUTINE (gate risk)"; fn.append(cid)
    elif emp == "ROUTINE" and majority == "COMPLEX":
        direction = "💸 false COMPLEX (cost only)"; fp.append(cid)
    else:
        direction = "✓"
    rows.append([cid, rubric, f"{emp}  {EMP_DETAIL[cid]}", majority, stable, direction])

print()
print(tabulate(rows, headers=["ID", "Rubric", f"Empirical (Haiku n_correct ×{TRIAGE_K})",
                              f"triage ×{TRIAGE_K}", "Stable?", "Error direction"],
               tablefmt="simple"))

n = len(CONTRACTS)
agree_rubric = sum(max(set(TRIAGE_VOTES[c["id"]]), key=TRIAGE_VOTES[c["id"]].count)
                   == TRIAGE_RUBRIC_LABELS[c["id"]] for c in CONTRACTS)
agree_emp = n - len(fn) - len(fp)
print(f"\n  triage vs rubric labels    : {agree_rubric}/{n}")
print(f"  triage vs empirical labels : {agree_emp}/{n}")
print(f"  ⛔ false ROUTINE (hard contract sent to Haiku — threatens the gate): "
      f"{len(fn)}  {fn if fn else ''}")
print(f"  💸 false COMPLEX (easy contract sent to the expensive model — costs money): "
      f"{len(fp)}  {fp if fp else ''}")
print(f"  triage cost: ${TRIAGE_COST:.5f} for {n}×{TRIAGE_K} classifications "
      f"(${TRIAGE_COST/(n*TRIAGE_K):.5f} each)")
print("\n  Read the two error columns separately. One costs a fraction of a cent; the other "
      "costs\n  the SLA. A single accuracy number for a router averages those together and "
      "tells you nothing.")

# %% [markdown]
# ### 🧭 Decision log — the ladder's surprises, and the k=5 run that settles them
#
# Read the ladder table above before this. Four results deserve calling out, and **three of them
# contradict the notebook's own suggested path.**
#
# **1. The anchor did not land on 100 — it landed on 94.** Rung 0 is v0's code, unmodified, run again.
# It measured p50 **39.9s** against the frozen baseline's **36.0s** — an **11% swing on identical
# code**. That is the whole argument for Stage 2 in one number: had the baseline not been frozen, that
# swing would have silently re-anchored every score in this notebook, in the direction that flatters a
# slow day. Treat ±6% on any score here as noise, and never quote a difference smaller than that.
#
# **2. Right-sizing `max_tokens` bought +9 points — essentially nothing.** The `CONFIG` comment says
# "right-size once output is disciplined (~1000)", which reads like a cost lever. It is not. **You pay
# for tokens generated, not tokens allocated.** Dropping the ceiling 8000 → 1000 changed cost by 0%
# because the schema-constrained response was already ~140 tokens. `max_tokens` is a **guardrail**, not
# a cost control — and per the truncation measurement earlier, setting it *too* tight is actively
# dangerous. Keep it right-sized for safety; do not book it as savings.
#
# **3. Routing to Haiku made things WORSE: −341 points and 12% *more* expensive.** The notebook's
# suggested path lists this as step 3. The router eval above shows why: triage classifies **4 of 6**
# contracts COMPLEX, so you pay a Haiku triage call on every contract *and still* pay Sonnet for most
# of them. Routing only pays when the routine share is large and the triage call is cheap relative to
# the gap it avoids. At this sample's mix, the tax exceeds the saving. On a real 248K estate the
# routine share is much higher — so this is a finding about *this corpus*, not a verdict on routing.
# Which is exactly why you measure instead of assuming.
#
# **4. Rungs 6–8 are confounded, so the ladder cannot settle them.** A cumulative ladder is the right
# tool for "does adding X help *given everything below it*" — but rungs 7 and 8 sit on top of rung 6's
# routing regression, so their deltas mix two effects. The ladder did its job by *finding* the
# regression; it is the wrong instrument for pricing what sits above it.
#
# So the finalists below re-test the top configuration at **k=5**, each varying one thing from the same
# base (rung 5 — the ladder's actual peak), with routing dropped:
#
# | | Base = structured · max_tokens 1000 · cached · Sonnet · effort=low |
# |---|---|
# | **F1** | base |
# | **F2** | + `derive_risk_tier` |
# | **F3** | + `parallel_workers=4` |
# | **F4** | + both |
# | **F5** | + `triage_routing` — routing re-tested cleanly, off the confounded rung |
# | **F6** | + `drop_evidence` — **priced, not adopted.** See the note below the results. |

# %%
BASE_FINALIST = {**V0_CONFIG,
                 "structured_single_pass": True, "max_tokens": 1000,
                 "cache_playbook": True,
                 "routine_model": MODEL_SONNET, "complex_model": MODEL_SONNET,
                 "effort": "low"}

FINALISTS = [
    ("F1 · base (ladder rung 5)",      {}),
    ("F2 · + derive risk_tier",        {"derive_risk_tier": True}),
    ("F3 · + parallel x4",             {"parallel_workers": 4}),
    ("F4 · + both",                    {"derive_risk_tier": True, "parallel_workers": 4}),
    ("F5 · + triage routing",          {"triage_routing": True, "routine_model": MODEL_HAIKU}),
    ("F6 · + drop evidence",           {"drop_evidence": True}),
]

FINALIST_K = 5


def run_finalists(finalists=FINALISTS, k=FINALIST_K):
    """Each finalist varies ONE thing from the SAME base — not cumulative. That is the
    difference between a ladder (does X help given everything below?) and a fan
    (what does X cost on its own?). Both are useful; confusing them is not."""
    out = []
    for label, delta in finalists:
        with with_config(**{**BASE_FINALIST, **delta}):
            rep = run_portfolio_k(clausescan_v1, k=k, workers=CONFIG["parallel_workers"])
        score = (0 if rep["acc_min"] < 0.90 else
                 round(50 * BASELINE["cost_per_contract"] / max(rep["cost_per_contract"], 1e-9)
                       + 50 * BASELINE["p50_s"] / max(rep["p50_s"], 1e-9)))
        out.append({"label": label, "score": score, "k": k,
                    "acc_mean": rep["acc_mean"], "acc_min": rep["acc_min"],
                    "acc_stdev": rep["acc_stdev"], "passk": rep["passk_accuracy"],
                    "p50_s": rep["p50_s"], "cost_per_contract": rep["cost_per_contract"],
                    "total_cost": rep["total_cost"], "n_errors": rep["n_errors"],
                    "wall": statistics.median(r["wall_s"] for r in rep["runs"])})
        print(f"{label:30s} score {score:>5}  acc {rep['acc_mean']*100:5.1f}% "
              f"(min {rep['acc_min']*100:5.1f}%, pass^k {rep['passk_accuracy']*100:5.1f}%)  "
              f"p50 {rep['p50_s']:4.1f}s  ${rep['cost_per_contract']:.4f}", flush=True)
    return out


print(f"Finalists at k={FINALIST_K} ({len(FINALISTS)} configs, one variable each)...")
FINAL = freeze_result("finalists", run_finalists)

print()
print(tabulate(
    [[f["label"], f["score"],
      f"{f['acc_mean']*100:.1f}%", f"{f['acc_min']*100:.1f}%",
      f"{f['acc_stdev']*100:.1f}pp", f"{f['passk']*100:.1f}%",
      f"{f['p50_s']:.1f}s", f"{f['wall']:.0f}s",
      f"${f['cost_per_contract']:.4f}", f["n_errors"]] for f in FINAL],
    headers=["Finalist", "Score", "acc mean", "acc min", "acc sd", "pass^k",
             "p50", "wall", "$/contract", "errs"], tablefmt="simple"))
print(f"\n  Total finalist spend: "
      f"${sum(f['total_cost'] for f in FINAL):.2f}  (k={FINALIST_K} each)")

best = max(FINAL, key=lambda f: f["score"])
print(f"  Highest score: {best['label']} at {best['score']}")
f6 = next((f for f in FINAL if "drop evidence" in f["label"]), None)
f1 = FINAL[0]
if f6:
    saving = (1 - f6["cost_per_contract"] / f1["cost_per_contract"]) * 100
    print(f"  Price of the audit trail: dropping `evidence` saves {saving:.0f}% "
          f"(+{f6['score'] - f1['score']} points). Priced, not adopted — see below.")

# %% [markdown]
# **Iterate.** Suggested path — but find your own; the leaderboard rewards imagination:
#
# 1. `structured_single_pass=True, max_tokens=1000` — watch output tokens and TTC collapse.
# 2. `cache_playbook=True` — run twice; the second pass shows the warm-cache economics.
# 3. `triage_routing=True, routine_model=MODEL_HAIKU, complex_model=MODEL_SONNET` — the
#    portfolio play.
# 4. `parallel_workers=4` — wall-clock for the working session.
# 5. Then go off-script: trim `EXTRACT_INSTRUCTION`, try `effort="low"` on Sonnet, route
#    HIGH-risk contracts to Opus for a second opinion, drop the playbook for routine contracts
#    and keep it for complex ones (what does that do to cache hits?), pre-warm the cache with a
#    `max_tokens=0` request at session start...
#
# When you think you're done: the holdout. Two contracts your pipeline has never seen — because
# an optimization that only works on the sample is called overfitting in our line of work, and
# a finding in the client's.

# %% [markdown]
# ### 🧭 Decision log — reading the fan, and the router eval that inverts the exercise
#
# **The router eval is the finding.** `triage()` is an LLM making a decision that gates
# accuracy, and nothing in the lab checks whether it is any good. Graded two ways:
#
# | | agreement |
# |---|---|
# | `triage()` vs my **rubric** labels (a-priori, from `TRIAGE_SYSTEM`'s own stated criteria) | **5/6** |
# | `triage()` vs the **empirical** labels (did Haiku actually get 5/5, on all 3 runs?) | **3/6** |
#
# The rubric score flatters it. The empirical score is the one with teeth, and by that measure
# the router is wrong half the time — because the premise underneath it is wrong. C-103
# (self-conflicting cap), C-104 (cap absent by silence), C-105 (an amendment deleting the cap)
# are the exercise's designed traps for a cheap model. **Haiku scored 5/5 on all three, on
# every run.** Its only miss anywhere was C-106 — which the rubric called ROUTINE.
#
# So the trap contracts no longer trap. The fixture encodes an assumption about model
# capability that has aged out, and the lever built on top of that assumption has nothing left
# to sell *on this corpus*.
#
# **Error direction is the part worth keeping.** 0 ⛔ false ROUTINE, 3 💸 false COMPLEX. The
# `except → COMPLEX` default in `triage()` fails toward accuracy, and every error it made landed
# on the expensive side. That is the design working: a false ROUTINE costs the SLA, a false
# COMPLEX costs $0.0008. A single accuracy number for a router averages those together and tells
# you nothing — which is why the table above reports them in separate columns.
#
# Note also that C-101 came back `COMPLEX, ROUTINE, ROUTINE` across three runs. **The router is
# itself nondeterministic**, and a majority vote hides that. A one-shot router eval would have
# called it stable.
#
# This explains ladder rung 6 exactly (−311 points, +12% cost): triage routes 4 of 6 to the
# expensive lane, 3 of them needlessly, so you pay the triage tax *and* the Sonnet price. The
# conclusion is **not** "routing is bad" — it is "routing pays only when the cheap model actually
# fails." On an estate where Haiku missed 30%, this measurement inverts. Ship the measurement,
# not the folklore.
#
# ---
#
# **Reading the finalist fan.** Six configs, one variable each off a shared base, k=5:
#
# - F1 2381 · F2 2338 · **F3 2384** · F4 2280 · F5 2283 — a 104-point spread, ≈4%.
# - The rung-0 anchor moved **94 → 98 across two runs of byte-identical code** — ≈4% on its own.
#
# So F1–F5 are **one indistinguishable cluster**. Picking a winner by score inside that band is
# reading noise and calling it a result. F6 is the only real separation: **2974, −20% cost**.
#
# **`derive_risk_tier` scored *lower* both times it appeared** (F2 vs F1, F4 vs F3). Inside the
# noise band, so not a refutation — but there is no measured support for it, and there is an
# argument against that the ladder cannot see: **deriving a field couples its error to its
# inputs.** Independent errors spread thinly across contracts; coupled errors cluster. A contract
# that gets `change_of_control` wrong now loses `risk_tier` too — two field misses instead of
# one, on a gate that is scored per contract. Shipping it **off**, against my own prior.
#
# **The gate never bound.** `acc sd = 0.0pp` at k=5 on every config, `pass^k = 100%` throughout.
# 30 checks is a saturated sample: every difference above is cost and latency, not quality. The
# honest statement of what this ladder proves is "no config degraded accuracy on six contracts" —
# not "accuracy is safe at 248,000."
#
# **F6 · drop `evidence` — priced, not adopted.** It is the highest score on the board: −20%
# cost, +593 points, gate untouched, because `evidence` is required by the schema and *not
# graded*. It is also the audit trail; the playbook has reviewers spot-check 10% of output
# against source text. The leaderboard rewards deleting the deliverable. **Keeping it, and
# leaving 593 points on the board on purpose** — that number is the price of the audit trail, and
# now it is a number the client can decide about instead of a thing that quietly went missing.
#
# **Final config = F3.** Not because 2384 beat 2381 — that gap is noise — but because parallel
# workers are the one difference in the cluster that is *not* noise: **wall-clock 21s → 10s at
# identical accuracy and identical p50.** Chosen for the clock the analyst actually experiences,
# with the score treated as a tie.

# %%
# ── FROZEN. No tuning past this line — that is the entire point of the holdout. ──────────
FINAL_CONFIG = {
    "triage_routing": False,                 # measured: −311 pts on this corpus (see log above)
    "routine_model": MODEL_SONNET,
    "complex_model": MODEL_SONNET,
    "cache_playbook": True,
    "structured_single_pass": True,
    "max_tokens": 1000,                      # guardrail, not a cost lever — rung 2 bought +8
    "effort": "low",
    "parallel_workers": 4,                   # wall-clock 21s → 10s; the only non-noise delta
    "derive_risk_tier": False,               # error-coupling argument; see log above
    "drop_evidence": False,                  # 593 points left on the board, deliberately
}

# `my_report` is NOT a fresh measurement. It is the F3 row from the frozen k=5 finalist fan —
# the same numbers the decision above was made from. Re-measuring here would quietly re-roll
# the dice and put a different number on the slide than the one that justified the choice.
_f3 = next(f for f in FINAL if f["label"].startswith("F3"))
my_report = {
    # acc_min, not acc_mean: the slide inherits the same conservative statistic the gate used.
    "accuracy": _f3["acc_min"],
    "p50_s": _f3["p50_s"],
    "cost_per_contract": _f3["cost_per_contract"],
    "k": _f3["k"],
}

print("CONFIG frozen →", json.dumps({k: str(v) for k, v in FINAL_CONFIG.items()}, indent=2))
print(f"\n  Final config measured at k={my_report['k']} on the 6-contract sample:")
print(f"    accuracy (min over k) {my_report['accuracy']*100:.0f}%   ·   "
      f"p50 {my_report['p50_s']:.1f}s   ·   ${my_report['cost_per_contract']:.4f}/contract")
print(f"  📋 leaderboard line →  SCORE {_f3['score']} · acc {my_report['accuracy']*100:.0f}% · "
      f"p50 {my_report['p50_s']:.1f}s · ${my_report['cost_per_contract']:.4f}/contract · "
      f"levers: schema-1pass+cache+sonnet+effort-low+parallel-x4")
print("\n  SLA: p50 ≤ 5s → "
      f"{'PASS' if my_report['p50_s'] <= 5 else 'FAIL'}   ·   accuracy ≥ 90% → "
      f"{'PASS' if my_report['accuracy'] >= 0.90 else 'FAIL'}   ·   COGS ≤ $0.02 → "
      f"{'PASS' if my_report['cost_per_contract'] <= 0.02 else 'FAIL'}")

# %%
HOLDOUT_CONTRACTS = [
    {
        "id": "C-201", "vendor": "Vanta Marketing Collective",
        "text": """MARKETING SERVICES AGREEMENT — Vanta Marketing Collective ("Agency") and Volta Industrial Group ("Client").

1. SERVICES. Brand, digital, and trade-show marketing services per approved statements of work.
2. TERM. Eighteen (18) months from the Effective Date, expiring automatically at the end of the term. Any renewal requires a new agreement executed by both parties.
3. FEES. Monthly retainer of $22,000 plus pre-approved pass-through costs.
4. LIMITATION OF LIABILITY. EACH PARTY'S TOTAL AGGREGATE LIABILITY UNDER THIS AGREEMENT IS LIMITED TO SEVENTY-FIVE THOUSAND U.S. DOLLARS ($75,000), EXCLUDING ONLY AMOUNTS PAYABLE UNDER SECTION 5 (INDEMNIFICATION FOR IP CLAIMS).
5. INDEMNIFICATION. Agency indemnifies Client against third-party IP claims arising from Agency-created materials.
6. ASSIGNMENT. Either party may assign this Agreement without consent upon written notice, including in connection with a change of control.
7. GOVERNING LAW. This Agreement is governed by the laws of the Province of Ontario, Canada.""",
    },
    {
        "id": "C-202", "vendor": "Quarry Industrial Supply Pte. Ltd.",
        "text": """INDUSTRIAL SUPPLY AGREEMENT — Quarry Industrial Supply Pte. Ltd. ("Quarry") and Volta Industrial Group ("Purchaser").

1. SUPPLY. Quarry supplies abrasives, fasteners, and consumables per released purchase orders.
2. TERM AND RENEWAL. Two (2) year initial term, automatically renewing for successive one-year terms unless either party gives ninety (90) days' written notice of non-renewal.
3. PRICING. Per the Annex A price file; freight prepaid for orders over S$5,000.
4. LIMITATION OF LIABILITY. QUARRY'S AND PURCHASER'S RESPECTIVE AGGREGATE LIABILITY ARISING OUT OF OR RELATING TO THIS AGREEMENT SHALL NOT EXCEED TWO MILLION U.S. DOLLARS ($2,000,000) IN THE AGGREGATE.
5. ASSIGNMENT AND CHANGE OF CONTROL. Neither party may assign or transfer this Agreement, including by merger, acquisition, or change of control, without the prior written consent of the other party. Any purported assignment without consent is void.
6. COMPLIANCE. Each party complies with applicable export-control and anti-corruption laws.
7. GOVERNING LAW. This Agreement is governed by the laws of Singapore.""",
    },
]

HOLDOUT_GOLD = {
    "C-201": {"auto_renewal": False, "change_of_control": False, "liability_cap_usd": 75000,
              "governing_law": "Ontario",   "risk_tier": "LOW"},
    "C-202": {"auto_renewal": True,  "change_of_control": True,  "liability_cap_usd": 2000000,
              "governing_law": "Singapore", "risk_tier": "MEDIUM"},
}

RUN_HOLDOUT = True    # CONFIG is frozen above; the holdout is now legal to run
HOLDOUT_K = 5         # 2 contracts × 5 fields = 10 checks. One miss IS exactly 90%.


def _measure_holdout():
    with with_config(**FINAL_CONFIG):
        rep = run_portfolio_k(clausescan_v1, k=HOLDOUT_K, contracts=HOLDOUT_CONTRACTS,
                              gold=HOLDOUT_GOLD, workers=FINAL_CONFIG["parallel_workers"])
    keep = ("k", "acc_mean", "acc_min", "acc_max", "acc_stdev", "passk_accuracy",
            "pass_at_k", "p50_s", "cost_per_contract", "total_cost", "n_errors", "n_checks")
    return {k: rep[k] for k in keep}


if RUN_HOLDOUT:
    HOLDOUT = freeze_result("holdout", _measure_holdout)
    print_k_report(HOLDOUT, "clausescan_v1 (FROZEN) — HOLDOUT")
    print("\n  Resolution warning: n=10 checks. Accuracy can only take the values "
          "100%, 90%, 80%, …\n  One miss lands exactly ON the 90% gate; two is a fail. "
          "This holdout can tell you\n  'not obviously broken'. It cannot tell you '95%'.")
    print("  No config was changed after seeing this. That is the only thing that makes it "
          "a holdout.")
else:
    print("Holdout armed. Set RUN_HOLDOUT = True when your CONFIG is final — "
          "no tuning against the holdout; that's the rule.")

# %% [markdown]
# ### 🧭 Decision log — the scale factor is an assumption, so measure it instead
#
# `ASSUMPTIONS["production_scale_factor"] = 8` carries more weight than any other number on the
# slide: it multiplies the estate COGS on **both** sides, so the entire savings figure is
# downstream of it. Its stated justification — *"input-dominated, cost scales ~linearly with
# document length"* — is wrong in a specific and quantifiable way.
#
# Cost per contract is **fixed cost + variable cost**. The playbook is fixed: an 8× longer
# contract does not come with an 8× longer playbook. Only the contract text is variable.
# (The notebook's own commentary calls it a *"5K-token playbook"*. It measures **7,882** — and
# the `cache_creation_input_tokens` counter from the caching lever says 7,882 too. Two
# independent instruments agree with each other and disagree with the prose; same class of
# error as the stale Sonnet price, found the same way.) So multiplying the whole `$/contract` by 8 inflates both sides — and it inflates the
# **v0 side hardest**, because v0 bills that 5.5K-token playbook at full price on *both* of its
# two calls, while the optimized pipeline reads it from cache at 0.1×. An assumption that
# inflates the "before" more than the "after" **manufactures savings**.
#
# That is a testable claim, and testing it costs about a dollar. The cell below pads each
# contract with synthetic boilerplate schedules that touch none of the five audited fields — so
# the gold labels stay valid, the contract gets *longer*, not *different* — and re-measures both
# pipelines on the padded corpus.
#
# Two honesty notes on the method. The padding is sized by characters and lands at ~6× tokens,
# not 8×, because legal boilerplate tokenizes more efficiently than the contracts do; the cell
# reports what was achieved rather than what was aimed at. And one measured point cannot draw a
# curve, so the cell fits the simplest defensible shape — cost ∝ length^e — and extrapolates to
# 8× from the measured exponent. The slide's assumption is that same shape with **e = 1.00**
# hardcoded for both pipelines. Replacing an assumed exponent with a measured one is the whole
# move; it is still an extrapolation, and the cell says so.

# %%
import math

# Synthetic boilerplate. Deliberately touches NONE of the five audited fields: no renewal or
# term language, no assignment or change of control, no liability or cap, no governing law or
# jurisdiction. So padding with it leaves GOLD valid — the contract gets longer, not different.
BOILERPLATE = [
    "Notices under this Agreement shall be in writing and delivered by hand, by nationally "
    "recognised overnight courier, or by electronic mail to the contact addresses recorded in "
    "the administrative annex, and shall be deemed received on the next business day following "
    "dispatch. Each party shall maintain a current notice contact and shall inform the other "
    "party of any change to that contact within ten (10) business days.",

    "If any provision of this Agreement is held unenforceable by a tribunal of competent "
    "authority, that provision shall be modified to the minimum extent necessary to render it "
    "enforceable, and the remaining provisions shall continue in full force and effect without "
    "further action by either party.",

    "Neither party shall be treated as in default for any delay or failure in performance "
    "caused by events beyond its reasonable control, including natural disaster, epidemic, "
    "labour disruption, utility failure, or action of a public authority, provided that the "
    "affected party gives prompt written notice and uses commercially reasonable efforts to "
    "resume performance.",

    "This Agreement may be executed in counterparts, each of which is deemed an original and "
    "all of which together constitute one instrument. Signatures transmitted electronically, "
    "including by recognised electronic signature platforms, have the same effect as original "
    "manuscript signatures.",

    "Each party is an independent contractor. Nothing in this Agreement creates a partnership, "
    "joint venture, agency, or employment relationship, and neither party has authority to bind "
    "the other or to incur obligations on the other's behalf.",

    "Confidential Information shall be marked as confidential where practicable and shall be "
    "used solely for purposes of performance. Upon written request the receiving party shall "
    "return or securely destroy Confidential Information in its possession and shall certify "
    "such destruction in writing within thirty (30) days.",

    "Each party shall maintain complete and accurate records relating to invoices issued and "
    "amounts paid for a period of not less than five (5) years. On not less than twenty (20) "
    "business days' written notice, and not more than once in any twelve-month period, either "
    "party may have such records examined by an independent accountant.",

    "Personnel and approved subcontractors performing work shall observe the receiving party's "
    "site rules, security procedures, and code of conduct. Each party remains fully responsible "
    "for the acts and omissions of its personnel and approved subcontractors as if they were "
    "its own.",

    "While on the other party's premises, each party's personnel shall comply with all posted "
    "health and safety requirements, complete any required site induction, and immediately "
    "report any incident, near miss, or unsafe condition to the site supervisor.",

    "Personal data processed in connection with this Agreement shall be handled in accordance "
    "with the data-handling annex, including access controls, encryption in transit and at "
    "rest, documented retention schedules, and notification of any confirmed security incident "
    "within seventy-two (72) hours of confirmation.",

    "Each party shall maintain a documented business continuity and disaster recovery plan, "
    "shall test that plan not less than annually, and shall provide a summary of the most "
    "recent test result on reasonable written request.",

    "Service reporting shall be provided monthly and shall include volumes processed, "
    "exceptions raised, root-cause summaries for any missed commitment, and the status of "
    "corrective actions agreed at the previous review.",

    "Changes to scope, deliverables, or agreed procedures shall be documented in a written "
    "change request, priced where applicable, and signed by an authorised representative of "
    "each party before the change is implemented.",

    "The parties shall hold a governance review not less than quarterly. Matters not resolved "
    "at that review shall be escalated in writing, first to the respective engagement leads and "
    "thereafter to an executive sponsor nominated by each party.",

    "No failure or delay in exercising any right under this Agreement operates as a waiver of "
    "that right, and no single or partial exercise precludes any further exercise of that or "
    "any other right.",

    "Headings are for convenience only and do not affect interpretation. References to the "
    "singular include the plural, and the words 'including' and 'includes' are to be read "
    "without limitation.",
]

SCHEDULE_TITLES = ["OPERATIONAL PROCEDURES", "SERVICE REPORTING AND GOVERNANCE",
                   "CHANGE CONTROL", "SECURITY AND DATA HANDLING",
                   "BUSINESS CONTINUITY", "GENERAL PROVISIONS"]


def pad_contract(contract: dict, factor: int) -> dict:
    """Pad with boilerplate schedules until the text is ~`factor`× as long."""
    text, target = contract["text"], len(contract["text"]) * factor
    parts, n, i = [text], len(text), 0
    while n < target:
        body = "\n".join(f"{j + 1}. {p}" for j, p in enumerate(BOILERPLATE))
        block = (f"\n\nSCHEDULE {chr(65 + i)} — "
                 f"{SCHEDULE_TITLES[i % len(SCHEDULE_TITLES)]}\n{body}")
        parts.append(block)
        n += len(block)
        i += 1
    return {**contract, "text": "".join(parts)}


SCALE_TARGET = 8
SCALED_CONTRACTS = [pad_contract(c, SCALE_TARGET) for c in CONTRACTS]


def _tok(user: str, system: str = "") -> int:
    # count_tokens rejects a whitespace-only system block, so omit it rather than pad it.
    kw = {"system": system} if system.strip() else {}
    return client.messages.count_tokens(
        model=MODEL_SONNET,
        messages=[{"role": "user", "content": user}], **kw).input_tokens


def _measure_scale():
    """Re-measure BOTH pipelines on the 8× corpus. Fixed vs variable cost is the whole point,
    so v0 (playbook billed twice at full price) and the final config (playbook cached at 0.1×)
    will not scale the same way — that difference is exactly what the slide is missing."""
    tok_1x = statistics.mean(_tok(EXTRACT_INSTRUCTION + c["text"]) for c in CONTRACTS)
    tok_8x = statistics.mean(_tok(EXTRACT_INSTRUCTION + c["text"]) for c in SCALED_CONTRACTS)
    tok_playbook = _tok("x", system=PLAYBOOK) - _tok("x")

    with with_config(**FINAL_CONFIG):
        fin = run_portfolio_k(clausescan_v1, k=3, contracts=SCALED_CONTRACTS, gold=GOLD,
                              workers=FINAL_CONFIG["parallel_workers"])
    with with_config(**V0_CONFIG):
        v0 = run_portfolio_k(clausescan_v0, k=1, contracts=SCALED_CONTRACTS, gold=GOLD)

    keep = ("k", "acc_mean", "acc_min", "acc_stdev", "passk_accuracy", "p50_s",
            "cost_per_contract", "total_cost", "n_errors", "n_checks")
    return {"tok_1x": tok_1x, "tok_8x": tok_8x, "tok_playbook": tok_playbook,
            "final": {k: fin[k] for k in keep}, "v0": {k: v0[k] for k in keep}}


SCALE = freeze_result("scale_test", _measure_scale)

# The padding targeted 8× but is sized by CHARACTERS, and legal boilerplate tokenizes more
# efficiently per character than the contracts do — so report what was ACHIEVED, not intended.
tok_ratio = SCALE["tok_8x"] / SCALE["tok_1x"]
raw_v0 = SCALE["v0"]["cost_per_contract"] / BASELINE["cost_per_contract"]
raw_fin = SCALE["final"]["cost_per_contract"] / my_report["cost_per_contract"]

# One measured point per pipeline, so fit the simplest curve with a defensible shape:
# cost ∝ length ** e.  e = 1 is exactly the slide's linear assumption; e = 0 is pure fixed
# cost. The measured exponent is the honest replacement for the assumed one — and the
# extrapolation to 8× is labelled as an extrapolation, because that is what it is.
elast_v0 = math.log(raw_v0) / math.log(tok_ratio)
elast_fin = math.log(raw_fin) / math.log(tok_ratio)
scale_v0 = SCALE_TARGET ** elast_v0
scale_fin = SCALE_TARGET ** elast_fin

print()
print(tabulate(
    [["user block (instruction + contract), tokens", f"{SCALE['tok_1x']:.0f}",
      f"{SCALE['tok_8x']:.0f}", f"{tok_ratio:.1f}×", f"target {SCALE_TARGET}×, achieved "
      f"{tok_ratio:.1f}×"],
     ["v0 $/contract", f"${BASELINE['cost_per_contract']:.4f}",
      f"${SCALE['v0']['cost_per_contract']:.4f}", f"{raw_v0:.2f}×",
      f"elasticity {elast_v0:.2f}"],
     ["final $/contract", f"${my_report['cost_per_contract']:.4f}",
      f"${SCALE['final']['cost_per_contract']:.4f}", f"{raw_fin:.2f}×",
      f"elasticity {elast_fin:.2f}"],
     ["final accuracy (min over k)", f"{my_report['accuracy']*100:.0f}%",
      f"{SCALE['final']['acc_min']*100:.0f}%", "—", "gate holds on longer documents"],
     ["final p50", f"{my_report['p50_s']:.1f}s", f"{SCALE['final']['p50_s']:.1f}s",
      f"{SCALE['final']['p50_s']/max(my_report['p50_s'],1e-9):.2f}×", "SLA is 5s"]],
    headers=["Metric", "1× (lab sample)", "padded corpus", "ratio", "note"],
    tablefmt="simple"))

print(f"\n  Fixed vs variable. The playbook measures {SCALE['tok_playbook']:,} input tokens and "
      f"does NOT grow with the\n  contract — the notebook's own text calls it a "
      f"\"5K-token playbook\"; `count_tokens` and the\n  `cache_creation_input_tokens` counter "
      f"from the caching lever agree with each other, not with\n  the prose. At 1× it dwarfs the "
      f"{SCALE['tok_1x']:.0f}-token user block; at {tok_ratio:.1f}× text it no longer does, "
      f"which is\n  exactly why the two pipelines scale differently.")
print(f"\n  Cost elasticity to document length (cost ∝ length**e):")
print(f"    v0     e = {elast_v0:.2f}  →  at {SCALE_TARGET}× length, {scale_v0:.2f}× cost")
print(f"    final  e = {elast_fin:.2f}  →  at {SCALE_TARGET}× length, {scale_fin:.2f}× cost")
print(f"    the slide's assumption is e = 1.00 for both  →  {SCALE_TARGET:.2f}× cost")
print(f"\n  Total scale-test spend: "
      f"${SCALE['v0']['total_cost'] + SCALE['final']['total_cost']:.2f}")
print("\n  Measured at one point and extrapolated by a power law — not a measurement AT 8×. "
      "That is\n  still a large improvement on a number that had no measurement behind it at "
      "all, and the\n  slide below prints the naive-8× version beside it so the correction is "
      "visible.")

# %% [markdown]
# # Part 6 · The steering-committee slide
#
# Optimization that stays in a notebook is a hobby. This cell turns your measured numbers into
# the before/after economics a steering committee actually reads — with the assumptions printed,
# because a benefits case without stated assumptions is the first thing a client audit
# committee throws out.

# %%
ASSUMPTIONS = {
    "contracts_in_estate": 248_000,
    "production_scale_factor": 8,   # SHIPPED ASSUMPTION — superseded below by measurement.
                                    # Kept so the slide can show what it would have claimed.
    "interactive_share": 0.15,      # analyst working sessions (full price, streamed)
    "batch_share": 0.85,            # overnight Batch API backfill (50% off)
    "fee_per_contract": 0.75,       # what HELVETICA bills per reviewed contract
}


def estate_cogs(cpc: float, scale: float, two_speed: bool, a=ASSUMPTIONS) -> float:
    per = cpc * scale
    if two_speed:
        per = per * a["interactive_share"] + per * a["batch_share"] * BATCH_DISCOUNT
    return per * a["contracts_in_estate"]


def steering_committee_slide(baseline: dict, optimized: dict, a=ASSUMPTIONS,
                             scale_before: float = None, scale_after: float = None):
    """scale_before / scale_after default to the shipped flat assumption. Pass the MEASURED
    factors to get a benefits case that survives being asked 'how do you know?'."""
    n = a["contracts_in_estate"]
    sb = a["production_scale_factor"] if scale_before is None else scale_before
    sa = a["production_scale_factor"] if scale_after is None else scale_after
    measured = scale_before is not None or scale_after is not None

    before = estate_cogs(baseline["cost_per_contract"], sb, two_speed=False, a=a)
    after = estate_cogs(optimized["cost_per_contract"], sa, two_speed=True, a=a)
    revenue = n * a["fee_per_contract"]

    rows = [
        ["Accuracy (audited fields)", f"{baseline['accuracy']*100:.0f}%",
         f"{optimized['accuracy']*100:.0f}%", "gate held"],
        ["p50 latency / contract", f"{baseline['p50_s']:.1f}s", f"{optimized['p50_s']:.1f}s",
         f"{baseline['p50_s']/max(optimized['p50_s'],1e-9):.1f}× faster"],
        ["Lab cost / contract", f"${baseline['cost_per_contract']:.4f}",
         f"${optimized['cost_per_contract']:.4f}",
         f"{baseline['cost_per_contract']/max(optimized['cost_per_contract'],1e-9):.1f}× cheaper"],
        ["Doc-length scale factor @8×", f"{sb:.2f}×", f"{sa:.2f}×",
         "measured elasticity" if measured else "ASSUMED"],
        ["Estate COGS (248K docs)", f"${before:,.0f}", f"${after:,.0f}",
         f"${before-after:,.0f} saved"],
        ["Engagement margin", f"{(revenue-before)/revenue*100:.0f}%",
         f"{(revenue-after)/revenue*100:.0f}%", "on $0.75/contract fee"],
        ["Interactive SLA (p50 ≤ 5s)",
         "PASS" if baseline["p50_s"] <= 5 else "FAIL",
         "PASS" if optimized["p50_s"] <= 5 else "FAIL", ""],
    ]
    print("PROJECT HELVETICA — Inference optimization: before / after")
    print(tabulate(rows, headers=["Metric", "v0 (inherited)", "Optimized", "Delta"],
                   tablefmt="grid"))
    return before, after


# ── The slide, on measured scale factors ────────────────────────────────────────────────
before, after = steering_committee_slide(BASELINE, my_report,
                                         scale_before=scale_v0, scale_after=scale_fin)

# ── What the shipped flat 8× assumption would have claimed ──────────────────────────────
naive_before = estate_cogs(BASELINE["cost_per_contract"], 8, two_speed=False)
naive_after = estate_cogs(my_report["cost_per_contract"], 8, two_speed=True)

print()
print(tabulate(
    [["Estate COGS before", f"${naive_before:,.0f}", f"${before:,.0f}",
      f"{(before/naive_before - 1)*100:+.0f}%"],
     ["Estate COGS after", f"${naive_after:,.0f}", f"${after:,.0f}",
      f"{(after/naive_after - 1)*100:+.0f}%"],
     ["Headline saving", f"${naive_before-naive_after:,.0f}", f"${before-after:,.0f}",
      f"{((before-after)/(naive_before-naive_after) - 1)*100:+.0f}%"]],
    headers=["Sensitivity", "flat 8× (shipped assumption)", "measured", "correction"],
    tablefmt="simple"))

print("\nStated assumptions")
print(f"  · contracts_in_estate = {ASSUMPTIONS['contracts_in_estate']:,} "
      f"(client-provided, not verified here)")
print(f"  · scale factors {scale_v0:.2f}× (v0) / {scale_fin:.2f}× (optimized) at 8× document "
      f"length —\n    extrapolated from MEASURED cost elasticities (e={elast_v0:.2f} / "
      f"{elast_fin:.2f}) on a padded corpus,\n    not assumed. They differ because v0 re-bills a "
      f"fixed {SCALE['tok_playbook']:,}-token playbook at full\n    price on both calls while the "
      f"optimized path reads it from cache at 0.1×; a fixed cost does\n    not scale with document "
      f"length, so one flat multiplier cannot serve both pipelines.")
print(f"  · lane split {ASSUMPTIONS['interactive_share']:.0%} interactive / "
      f"{ASSUMPTIONS['batch_share']:.0%} batch at 50% off — a PLANNED split, not an observed one")
print(f"  · fee_per_contract = ${ASSUMPTIONS['fee_per_contract']:.2f} (commercial input)")
print(f"  · pricing verified {PRICING_VERIFIED_ON} against {PRICING_SOURCE}")
print(f"  · accuracy quoted is the MINIMUM over k={my_report['k']} runs, not the mean; "
      f"holdout n=10 checks")
print("\n  Not evidenced by this lab: that 6 sample contracts + 2 holdout contracts represent "
      "248,000\n  documents. Every accuracy claim above is bounded by that sample, and no "
      "amount of\n  k-repetition fixes it. The next real step is a stratified sample of the "
      "actual estate.")

# %% [markdown]
# # Wrap-up — the levers, as a client checklist
#
# Walk into any inference-cost conversation with this list and you will earn your rate:
#
# 1. **Measure first** — TTFT, TTC, OTPS, cache-aware $/unit. No baseline, no benefits case.
# 2. **Model portfolio + routing** — cheap models for volume, expensive models for judgment, a
#    triage pass deciding which is which.
# 3. **Cache the frozen prefix** — playbooks, tool definitions, system prompts. Verify with
#    `cache_read_input_tokens`, mind the per-model minimums, keep volatile bytes out of the prefix.
# 4. **Discipline the output** — structured outputs as the deliverable spec, right-sized
#    `max_tokens`, `effort` dialed to the task (never on Haiku).
# 5. **Collapse round trips, stream the rest** — every serial call is latency and re-billed
#    context; TTFT is the UX number.
# 6. **Parallelize with care** — warm the cache, then fan out inside your rate limits.
# 7. **Two-speed architecture** — interactive lane for humans, Batch API (50% off) for the dark
#    backlog. Decide per workload, not per project.
# 8. **Gate everything on accuracy** — a quality eval runs *before* the optimization victory lap.
#    (Yesterday's evals session is the other half of this lab.)
#
# **Docs to bookmark:** platform.claude.com/docs → Prompt caching · Batch processing ·
# Structured outputs · Effort · Streaming · Pricing.
