"""Autonomous 5-hour research, backtest-gated optimization, and hot-deploy loop.

Designed to run inside Optibook's browser Dev Environment (or locally with --mock)
while respecting Optibook's 1-connection-per-team, 25 req/s, and cancel-on-disconnect rules:
  1. Keeps ONE live Champion `bot.runner` process running and recording CSV + telemetry.jsonl.
  2. Every `--cycle-min` minutes (default 20m), snapshots the live chunk without disconnecting.
  3. Runs `bot/muster.py` + `bot/backtest.py` baseline, then asks Gemini API to diagnose & patch.
  4. Gates every edit behind `compileall`, `tests/test_bot.py`, and head-to-head `bot/backtest.py`.
  5. Only restarts the live bot (via clean SIGINT) if the Candidate beats the Champion.
  6. Logs insights to `.brain/INSIGHTS.md` + `.brain/autoloop_log.md` and syncs via `brain-push.sh`.

Usage:
    python3 -m bot.autoloop --hours 5 --cycle-min 20
    python3 -m bot.autoloop --mock --hours 0.1 --cycle-min 1   # dry-run test
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None  # type: ignore

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

_LOG_DIR = os.path.join(_ROOT, "data")
os.makedirs(_LOG_DIR, exist_ok=True)
_LOG_FILE = os.path.join(_LOG_DIR, "autoloop_orchestrator.log")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(_LOG_FILE, mode="a", encoding="utf-8"),
    ],
)
logger = logging.getLogger("abmms-autoloop")

PROTECTED_PATHS = {"challenge", "bot/autoloop.py", ".brain/GOAL.md", ".env"}
PROTECTED_FILES = {os.path.join("bot", "autoloop.py"), ".env"}


@dataclass
class BacktestMetrics:
    pnl: float
    h1: float
    h2: float
    drawdown: float
    fills_passive: int
    fills_aggressive: int
    max_req_sec: int
    stopped: Optional[str]
    raw_report: str


def load_dotenv(env_path: Optional[str] = None) -> None:
    """Load key=value pairs from .env into os.environ if not already set."""
    path = env_path or os.path.join(_ROOT, ".env")
    if not os.path.isfile(path):
        return
    with open(path, encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = val


def parse_backtest_report(text: str) -> Optional[BacktestMetrics]:
    """Parse metrics from `bot/backtest.py` markdown stdout."""
    if not text:
        return None
    stopped_m = re.search(r"\*\*ABBRUCH wie auf Optibook:\s*(.+?)\*\*", text)
    stopped = stopped_m.group(1).strip() if stopped_m else None

    pnl_m = re.search(r"\|\s*PnL gesamt\s*\|\s*([+-]?\d+(?:\.\d+)?)\s*\|", text)
    if not pnl_m:
        return None
    half_m = re.search(
        r"\|\s*1\. Haelfte / 2\. Haelfte\s*\|\s*([+-]?\d+(?:\.\d+)?)\s*/\s*([+-]?\d+(?:\.\d+)?)\s*\|",
        text,
    )
    dd_m = re.search(r"\|\s*Groesster Rueckgang\s*\|\s*([+-]?\d+(?:\.\d+)?)\s*\|", text)
    fills_m = re.search(r"\|\s*Fills \(passiv / aggressiv\)\s*\|\s*(\d+)\s*/\s*(\d+)\s*\|", text)
    req_m = re.search(r"\|\s*Max\. Anfragen pro Sekunde\s*\|\s*(\d+)\s*\|", text)

    return BacktestMetrics(
        pnl=float(pnl_m.group(1)),
        h1=float(half_m.group(1)) if half_m else 0.0,
        h2=float(half_m.group(2)) if half_m else 0.0,
        drawdown=float(dd_m.group(1)) if dd_m else 0.0,
        fills_passive=int(fills_m.group(1)) if fills_m else 0,
        fills_aggressive=int(fills_m.group(2)) if fills_m else 0,
        max_req_sec=int(req_m.group(1)) if req_m else 0,
        stopped=stopped,
        raw_report=text,
    )


def should_promote(
    baseline: Optional[BacktestMetrics],
    candidate: Optional[BacktestMetrics],
    min_improvement: float = 0.5,
    max_extra_drawdown: float = 100.0,
) -> Tuple[bool, str]:
    """Decide whether candidate beats baseline without violating Optibook safety."""
    if candidate is None:
        return False, "candidate backtest produced no parseable metrics"
    if candidate.stopped:
        return False, f"candidate disconnected in backtest: {candidate.stopped}"
    if candidate.max_req_sec > 24:
        return False, f"candidate req/s too high ({candidate.max_req_sec} > 24)"
    if baseline is None:
        return True, f"no baseline metrics; candidate pnl={candidate.pnl:+.1f}"
    if baseline.stopped and not candidate.stopped:
        return True, f"candidate fixed baseline disconnect ({baseline.stopped})"
    if candidate.pnl <= baseline.pnl + min_improvement:
        return (
            False,
            f"candidate pnl {candidate.pnl:+.1f} did not beat baseline {baseline.pnl:+.1f} (+{min_improvement:.1f})",
        )
    if candidate.drawdown < baseline.drawdown - max_extra_drawdown:
        return (
            False,
            f"candidate drawdown {candidate.drawdown:.1f} worse than baseline {baseline.drawdown:.1f}",
        )
    return True, f"candidate pnl {candidate.pnl:+.1f} beat baseline {baseline.pnl:+.1f} (dd={candidate.drawdown:.1f})"


def summarize_telemetry(telemetry_path: str) -> Dict[str, Any]:
    """Compute a compact PnL, drawdown, position, and markout summary from telemetry.jsonl."""
    rows: List[Dict[str, Any]] = []
    if os.path.isfile(telemetry_path):
        with open(telemetry_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    if not rows:
        return {"samples": 0}

    pnls = [float(r.get("pnl", 0.0)) for r in rows]
    peak = pnls[0]
    max_dd = 0.0
    for p in pnls:
        peak = max(peak, p)
        max_dd = min(max_dd, p - peak)

    last = rows[-1]
    return {
        "samples": len(rows),
        "start_pnl": pnls[0],
        "end_pnl": pnls[-1],
        "delta_pnl": round(pnls[-1] - pnls[0], 2),
        "max_drawdown": round(max_dd, 2),
        "last_pos": last.get("pos", {}),
        "last_mk5s": last.get("mk5s", {}),
        "last_paused": last.get("paused", []),
        "drawdown_halted": any(bool(r.get("drawdown_halted")) for r in rows),
    }


def latest_active_data_dir(primary_dir: str, started_after: float) -> str:
    """Return the freshest live data folder (handles bot.runner --nacht chunk rotation)."""
    candidates = [primary_dir]
    data_root = os.path.join(_ROOT, "data")
    if os.path.isdir(data_root):
        for name in os.listdir(data_root):
            if name.startswith("live_"):
                full = os.path.join(data_root, name)
                if os.path.isdir(full) and os.path.getmtime(full) >= started_after:
                    candidates.append(full)
    valid = [d for d in candidates if os.path.isfile(os.path.join(d, "books.csv"))]
    if not valid:
        return primary_dir
    return max(valid, key=lambda d: os.path.getmtime(os.path.join(d, "books.csv")))


def snapshot_live_data(live_dir: str, snapshot_dir: str, started_after: float = 0.0) -> bool:
    """Copy current CSVs and telemetry.jsonl into an isolated snapshot folder."""
    src_dir = latest_active_data_dir(live_dir, started_after) if started_after > 0 else live_dir
    os.makedirs(snapshot_dir, exist_ok=True)
    copied = False
    for name in ("books.csv", "trades.csv", "news.csv", "telemetry.jsonl"):
        src = os.path.join(src_dir, name)
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(snapshot_dir, name))
            copied = True
    books_path = os.path.join(snapshot_dir, "books.csv")
    if not os.path.isfile(books_path):
        return False
    with open(books_path, encoding="utf-8", errors="ignore") as f:
        lines = sum(1 for _ in f)
    return copied and lines >= 5


def run_cmd(args: List[str], timeout: int = 300) -> Tuple[int, str]:
    """Run a subprocess in repo root and return (returncode, combined_output)."""
    try:
        proc = subprocess.run(
            args,
            cwd=_ROOT,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, f"TIMEOUT after {timeout}s: {' '.join(args)}"


def call_gemini(prompt: str, api_key: str, base_url: str, model: str = "azure/gpt-6.2-astra") -> Dict[str, Any]:
    """Call OpenAI-compatible LLM gateway (SDK if installed, else stdlib urllib) and return JSON."""
    raw_text = ""
    if OpenAI is not None:
        client = OpenAI(api_key=api_key, base_url=base_url)
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.25,
                response_format={"type": "json_object"},
                timeout=120.0,
            )
            raw_text = (response.choices[0].message.content or "").strip() if response.choices else ""
        except Exception as exc:
            raise RuntimeError(f"OpenAI API call failed: {exc}")
    else:
        url = base_url.rstrip("/") + "/chat/completions"
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.25,
            "response_format": {"type": "json_object"},
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            err_body = exc.read().decode("utf-8", errors="ignore")[:300]
            raise RuntimeError(f"LLM HTTP {exc.code}: {err_body}") from exc
        except Exception as exc:
            raise RuntimeError(f"LLM HTTP request failed: {exc}") from exc
        choices = body.get("choices") or []
        if choices:
            raw_text = str(choices[0].get("message", {}).get("content") or "").strip()

    if not raw_text:
        raise RuntimeError("LLM returned empty response")

    # Trim markdown code fence if present
    if raw_text.startswith("```"):
        raw_text = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw_text, flags=re.DOTALL).strip()

    return json.loads(raw_text)


def read_text_safe(rel_path: str, max_chars: int = 12000) -> str:
    path = os.path.join(_ROOT, rel_path)
    if not os.path.isfile(path):
        return ""
    with open(path, encoding="utf-8", errors="ignore") as f:
        data = f.read()
    if len(data) > max_chars:
        return data[:max_chars] + "\n... [truncated]"
    return data


def build_agent_prompt(
    cycle_num: int,
    telemetry_summary: Dict[str, Any],
    muster_output: str,
    baseline: Optional[BacktestMetrics],
    history_tail: str,
    nacht_report: str = "",
) -> str:
    """Assemble compact high-signal context for Gemini, hot-reloading AUTOLOOP_AGENTS.md."""
    playbook = read_text_safe("AUTOLOOP_AGENTS.md", 8000) or (
        "You are an autonomous quant trading engineer optimizing an Optibook ABMM-S bot. "
        "Return ONLY valid JSON with keys: diagnosis, insights, edits, summary."
    )
    goal = read_text_safe(".brain/GOAL.md", 8000)
    insights = read_text_safe(".brain/INSIGHTS.md", 8000)
    notes = read_text_safe("challenge/optibook_website_notizen.md", 8000)
    config_py = read_text_safe("bot/config.py", 12000)
    signals_py = read_text_safe("bot/signals.py", 60000)
    execution_py = read_text_safe("bot/execution.py", 45000)
    runner_py = read_text_safe("bot/runner.py", 30000)

    baseline_str = baseline.raw_report if baseline else "No baseline backtest available."
    nacht_section = f"\n## Multi-Hour Nacht Report\n{nacht_report[:3000]}\n" if nacht_report else ""
    return f"""{playbook}

---

## Cycle {cycle_num} Live Telemetry
{json.dumps(telemetry_summary, indent=2)}

## Baseline Backtest on Latest Market Chunk
{baseline_str}
{nacht_section}
## Muster Empirical Signal Check
{muster_output[:3000]}

## Recent Autoloop History
{history_tail}

## Second Brain Context
### .brain/GOAL.md
{goal}

### .brain/INSIGHTS.md
{insights}

### challenge/optibook_website_notizen.md
{notes}

## Current Source Code
### bot/config.py
```python
{config_py}
```

### bot/signals.py
```python
{signals_py}
```

### bot/execution.py
```python
{execution_py}
```

### bot/runner.py
```python
{runner_py}
```"""


def apply_edits(edits: List[Dict[str, str]], backup_dir: str) -> Tuple[List[str], List[str]]:
    """Validate and apply search/replace edits everywhere except protected paths, saving backups first."""
    os.makedirs(backup_dir, exist_ok=True)
    modified: List[str] = []
    errors: List[str] = []

    for idx, edit in enumerate(edits):
        rel = os.path.normpath(edit.get("path", "").strip())
        search = edit.get("search", "")
        replace = edit.get("replace", "")
        
        # Check protection rules: reject if in protected paths or contains path traversal
        if ".." in rel:
            errors.append(f"edit[{idx}] path traversal blocked: {rel}")
            continue
        
        # Reject if path starts with any protected prefix
        is_protected = any(rel.startswith(p) for p in PROTECTED_PATHS)
        if is_protected or rel in PROTECTED_FILES:
            errors.append(f"edit[{idx}] protected path: {rel}")
            continue
        
        abs_path = os.path.join(_ROOT, rel)
        if not os.path.isfile(abs_path):
            errors.append(f"edit[{idx}] missing file: {rel}")
            continue
        if not search:
            errors.append(f"edit[{idx}] empty search string for {rel}")
            continue

        with open(abs_path, encoding="utf-8") as f:
            content = f.read()
        if search not in content:
            errors.append(f"edit[{idx}] search block not found in {rel}")
            continue

        bkp_path = os.path.join(backup_dir, rel.replace(os.sep, "__"))
        if not os.path.isfile(bkp_path):
            shutil.copy2(abs_path, bkp_path)

        new_content = content.replace(search, replace, 1)
        with open(abs_path, "w", encoding="utf-8") as f:
            f.write(new_content)
        if rel not in modified:
            modified.append(rel)

    return modified, errors


def restore_backup(backup_dir: str, modified: List[str]) -> None:
    """Restore backed-up files after a failed or rejected candidate."""
    for rel in modified:
        bkp_path = os.path.join(backup_dir, rel.replace(os.sep, "__"))
        if os.path.isfile(bkp_path):
            shutil.copy2(bkp_path, os.path.join(_ROOT, rel))


def sync_second_brain(
    cycle_num: int,
    diagnosis: str,
    insights: List[str],
    outcome: str,
    summary: str,
    push: bool = True,
) -> None:
    """Append autoloop learnings to .brain/INSIGHTS.md + .brain/autoloop_log.md and push."""
    ts = datetime.now().strftime("%H:%M")
    log_path = os.path.join(_ROOT, ".brain", "autoloop_log.md")
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(f"\n### Cycle {cycle_num} ({ts}) — {outcome}\n")
        f.write(f"- **Diagnosis**: {diagnosis}\n")
        for ins in insights:
            f.write(f"- **Insight**: {ins}\n")

    if insights:
        insights_path = os.path.join(_ROOT, ".brain", "INSIGHTS.md")
        existing = read_text_safe(".brain/INSIGHTS.md", 50000)
        marker = "## 3. Autoloop Insights"
        if marker not in existing:
            existing = existing.rstrip() + f"\n\n{marker}\n"
        before, after = existing.split(marker, 1)
        auto_lines = [ln for ln in after.strip().splitlines() if ln.strip()]
        for ins in insights[:2]:
            auto_lines.append(f"- [ ] **[Autoloop C{cycle_num}]** ({ts}): {ins}")
        # Keep .brain/INSIGHTS.md lean (last 8 autoloop bullets)
        auto_lines = auto_lines[-8:]
        new_text = before.rstrip() + f"\n\n{marker}\n\n" + "\n".join(auto_lines) + "\n"
        with open(insights_path, "w", encoding="utf-8") as f:
            f.write(new_text)

    if push:
        rc, out = run_cmd(["./scripts/brain-push.sh", f"autoloop c{cycle_num}: {summary[:45]}"], timeout=60)
        if rc != 0:
            logger.warning("brain-push.sh failed (rc=%d): %s", rc, out.strip()[:200])
        else:
            logger.info("Second brain synced to main (cycle %d)", cycle_num)


def commit_and_push_promoted(cycle_num: int, modified: List[str], summary: str) -> None:
    """Commit promoted code changes to the current branch (including main) and push."""
    try:
        rc, cur_branch = run_cmd(["git", "rev-parse", "--abbrev-ref", "HEAD"], timeout=10)
        cur_branch = cur_branch.strip() or "main"

        # Stage modified files
        for rel_path in modified:
            abs_path = os.path.join(_ROOT, rel_path)
            if os.path.isfile(abs_path):
                run_cmd(["git", "add", abs_path], timeout=10)

        # Commit
        commit_msg = f"autoloop c{cycle_num}: {summary[:50]}"
        rc, out = run_cmd(["git", "commit", "-m", commit_msg], timeout=30)
        if rc != 0:
            logger.warning("git commit failed (rc=%d): %s", rc, out.strip()[:200])
            return

        # Push current branch (main or personal branch)
        rc, out = run_cmd(["git", "push", "origin", cur_branch], timeout=60)
        if rc != 0:
            logger.warning("git push to %s failed (rc=%d): %s", cur_branch, rc, out.strip()[:200])
        else:
            logger.info("Promoted changes pushed to origin/%s (cycle %d)", cur_branch, cycle_num)
    except Exception as exc:
        logger.warning("Exception during git commit/push: %s", exc)


class LiveRunnerManager:
    """Manages the single-connection live `bot.runner --nacht` subprocess."""

    def __init__(self, mock_mode: bool, base_dir: str, nacht_chunk_min: float = 60.0) -> None:
        self.mock_mode = mock_mode
        self.base_dir = base_dir
        self.nacht_dir = os.path.join(base_dir, "nacht")
        self.nacht_chunk_min = nacht_chunk_min
        self.proc: Optional[subprocess.Popen[Any]] = None
        self.live_dir: Optional[str] = None
        self.started_at: float = 0.0
        self._log_file: Optional[Any] = None
        self._run_idx = 0

    def start(self) -> None:
        self._run_idx += 1
        self.started_at = time.time()
        self.live_dir = os.path.join(self.base_dir, f"live_{self._run_idx:03d}_{datetime.now().strftime('%H%M%S')}")
        os.makedirs(self.live_dir, exist_ok=True)
        os.makedirs(self.nacht_dir, exist_ok=True)
        cmd = [
            sys.executable,
            "-m",
            "bot.runner",
            "--nacht",
            "--chunk-min",
            str(self.nacht_chunk_min),
            "--log-dir",
            self.live_dir,
        ]
        if self.mock_mode:
            cmd.append("--mock")
        sub_env = os.environ.copy()
        sub_env["MOCK_MODE"] = "true" if self.mock_mode else "false"
        sub_env["NACHT_DIR"] = self.nacht_dir
        log_path = os.path.join(self.live_dir, "runner.log")
        self._log_file = open(log_path, "a", encoding="utf-8")
        self.proc = subprocess.Popen(cmd, cwd=_ROOT, env=sub_env, stdout=self._log_file, stderr=subprocess.STDOUT)
        logger.info(
            "Started live Champion runner --nacht (pid=%d, chunk=%sm) -> %s",
            self.proc.pid,
            self.nacht_chunk_min,
            self.live_dir,
        )

    def ensure_alive(self) -> None:
        if self.proc is None or self.proc.poll() is not None:
            rc = self.proc.returncode if self.proc else None
            logger.warning("Live runner exited (rc=%s); restarting in 10s...", rc)
            time.sleep(10)
            self.start()

    def stop_gracefully(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            logger.info("Sending SIGINT to live runner (pid=%d) for clean order cancel...", self.proc.pid)
            self.proc.send_signal(signal.SIGINT)
            try:
                self.proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                logger.warning("Live runner did not exit in 15s; terminating")
                self.proc.terminate()
                self.proc.wait(timeout=5)
        if self._log_file is not None:
            try:
                self._log_file.close()
            except Exception:
                pass
            self._log_file = None
        self.proc = None


def main() -> None:
    parser = argparse.ArgumentParser(description="5-hour autonomous LLM + backtest-gated trading loop")
    parser.add_argument("--hours", type=float, default=5.0, help="Total runtime in hours (default 5.0)")
    parser.add_argument("--cycle-min", type=float, default=20.0, help="Minutes per optimization cycle (default 20)")
    parser.add_argument("--nacht-chunk-min", type=float, default=60.0, help="Minutes per --nacht rotation chunk (default 60)")
    parser.add_argument("--mock", action="store_true", help="Run against MockExchange for dry-run testing")
    parser.add_argument("--no-push", action="store_true", help="Skip ./scripts/brain-push.sh")
    parser.add_argument("--rollback-loss", type=float, default=-150.0, help="Auto-rollback if promoted live delta PnL <= this")
    args = parser.parse_args()

    load_dotenv()
    
    # Load LLM gateway config from .env
    base_url = os.getenv("LLM_GATEWAY_BASE_URL", "https://llm.orange.sixt.com/v1").strip()
    api_key = os.getenv("LLM_GATEWAY_API_KEY", "").strip()
    model = os.getenv("LLM_GATEWAY_MODEL", "azure/gpt-6.2-astra").strip()
    
    if not api_key and not args.mock:
        logger.error("LLM_GATEWAY_API_KEY is missing in environment / .env")
        sys.exit(1)

    run_root = os.path.join(_ROOT, "data", "autoloop_" + datetime.now().strftime("%d_%H%M%S"))
    os.makedirs(run_root, exist_ok=True)
    runner = LiveRunnerManager(mock_mode=args.mock, base_dir=run_root, nacht_chunk_min=args.nacht_chunk_min)

    deadline = time.monotonic() + args.hours * 3600.0
    cycle_sec = max(15.0, args.cycle_min * 60.0)
    last_promoted_backup: Optional[Tuple[str, List[str]]] = None

    try:
        runner.start()
        cycle_num = 0
        while time.monotonic() < deadline:
            wait_until = min(deadline, time.monotonic() + cycle_sec)
            while time.monotonic() < wait_until:
                time.sleep(min(5.0, max(0.5, wait_until - time.monotonic())))
                runner.ensure_alive()

            cycle_num += 1
            logger.info("=== Autoloop Cycle %d ===", cycle_num)
            if not runner.live_dir:
                continue

            snapshot_dir = os.path.join(run_root, "snapshots", f"cycle_{cycle_num:03d}")
            if not snapshot_live_data(runner.live_dir, snapshot_dir, started_after=runner.started_at):
                logger.warning("Cycle %d skipped: insufficient market data in %s", cycle_num, runner.live_dir)
                continue

            telem = summarize_telemetry(os.path.join(snapshot_dir, "telemetry.jsonl"))
            logger.info("Live telemetry summary: %s", telem)

            # Auto-rollback if a newly promoted version bled badly in live trading
            if last_promoted_backup is not None and telem.get("samples", 0) >= 2:
                delta_pnl = float(telem.get("delta_pnl", 0.0))
                if delta_pnl <= args.rollback_loss or telem.get("drawdown_halted"):
                    bkp_dir, mod_files = last_promoted_backup
                    logger.warning("AUTO-ROLLBACK triggered (delta_pnl=%.2f) -> restoring %s", delta_pnl, bkp_dir)
                    restore_backup(bkp_dir, mod_files)
                    runner.stop_gracefully()
                    runner.start()
                    last_promoted_backup = None
                    sync_second_brain(
                        cycle_num,
                        f"Promoted strategy lost {delta_pnl:.2f} in live trading; auto-rolled back.",
                        [f"Auto-rollback triggered at delta_pnl={delta_pnl:.2f}; avoid aggressive change from cycle {cycle_num - 1}."],
                        "ROLLBACK",
                        "auto-rollback on live loss",
                        push=not args.no_push,
                    )
                    continue
                last_promoted_backup = None

            _, muster_out = run_cmd([sys.executable, "bot/muster.py", snapshot_dir], timeout=120)
            _, base_out = run_cmd(
                [sys.executable, "bot/backtest.py", snapshot_dir, "--strategy", "runner:BacktestBot"],
                timeout=180,
            )
            baseline = parse_backtest_report(base_out)
            logger.info("Baseline backtest: %s", f"pnl={baseline.pnl:+.1f}" if baseline else "N/A")

            if not api_key:
                logger.info("No LLM_GATEWAY_API_KEY in --mock dry-run; skipping LLM API call")
                continue

            history_tail = read_text_safe(".brain/autoloop_log.md", 3000)[-2000:]
            nacht_report_path = os.path.join(runner.nacht_dir, "nacht_bericht.md")
            nacht_report = ""
            if os.path.isfile(nacht_report_path):
                with open(nacht_report_path, encoding="utf-8", errors="ignore") as nf:
                    nacht_report = nf.read()
            prompt = build_agent_prompt(cycle_num, telem, muster_out, baseline, history_tail, nacht_report=nacht_report)
            try:
                proposal = call_gemini(prompt, api_key=api_key, base_url=base_url, model=model)
            except Exception as exc:
                logger.warning("LLM API call failed in cycle %d: %s", cycle_num, exc)
                continue

            diagnosis = str(proposal.get("diagnosis", "")).strip()
            insights = [str(x).strip() for x in (proposal.get("insights") or []) if str(x).strip()]
            edits = proposal.get("edits") or []
            summary = str(proposal.get("summary", "strategy update")).strip()

            if not edits:
                sync_second_brain(cycle_num, diagnosis, insights, "NO_CHANGE", summary, push=not args.no_push)
                continue

            bkp_dir = os.path.join(run_root, "backups", f"cycle_{cycle_num:03d}")
            modified, edit_errs = apply_edits(edits, bkp_dir)
            if edit_errs or not modified:
                logger.warning("Edit application failed: %s", edit_errs)
                restore_backup(bkp_dir, modified)
                sync_second_brain(cycle_num, diagnosis, insights, f"EDIT_ERROR ({edit_errs})", summary, push=not args.no_push)
                continue

            rc_comp, comp_out = run_cmd([sys.executable, "-m", "compileall", "-q", "bot", "tests"], timeout=60)
            rc_test, test_out = run_cmd([sys.executable, "-m", "unittest", "tests/test_bot.py"], timeout=120)
            if rc_comp != 0 or rc_test != 0:
                logger.warning("Candidate failed compile/tests; restoring backup.\n%s\n%s", comp_out, test_out[-500:])
                restore_backup(bkp_dir, modified)
                sync_second_brain(cycle_num, diagnosis, insights, "REJECTED_TESTS", summary, push=not args.no_push)
                continue

            _, cand_out = run_cmd(
                [sys.executable, "bot/backtest.py", snapshot_dir, "--strategy", "runner:BacktestBot"],
                timeout=180,
            )
            candidate = parse_backtest_report(cand_out)
            promote, reason = should_promote(baseline, candidate)
            if promote:
                logger.info("PROMOTING candidate: %s", reason)
                last_promoted_backup = (bkp_dir, modified)
                runner.stop_gracefully()
                runner.start()
                # Commit promoted changes to personal branch
                commit_and_push_promoted(cycle_num, modified, summary)
                sync_second_brain(cycle_num, diagnosis, insights, f"PROMOTED ({reason})", summary, push=not args.no_push)
            else:
                logger.info("REJECTED candidate: %s", reason)
                restore_backup(bkp_dir, modified)
                sync_second_brain(cycle_num, diagnosis, insights, f"REJECTED ({reason})", summary, push=not args.no_push)
    except KeyboardInterrupt:
        logger.info("Autoloop interrupted by user")
    finally:
        runner.stop_gracefully()


if __name__ == "__main__":
    main()
