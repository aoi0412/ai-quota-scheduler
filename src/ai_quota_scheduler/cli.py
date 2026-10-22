"""``aqs`` command line: daemon, status, queue, kill switch, statusline hook."""

from __future__ import annotations

import argparse
import json
import logging
import sys
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from ai_quota_scheduler.adapters.claude_statusline import statusline_hook
from ai_quota_scheduler.config import SchedulerConfig, load_config
from ai_quota_scheduler.daemon import INITIAL_COEF, KILL_FILENAME, build_context, run_forever
from ai_quota_scheduler.models import FIVE_HOUR, SEVEN_DAY
from ai_quota_scheduler.store import Store

__all__ = ["DEFAULT_CONFIG", "load_cli_config", "main"]

log = logging.getLogger(__name__)

DEFAULT_CONFIG = Path("~/.config/ai-quota-scheduler/config.toml").expanduser()


def load_cli_config(path: Path) -> SchedulerConfig:
    return load_config(path) if path.exists() else SchedulerConfig()


def _status(cfg: SchedulerConfig) -> dict[str, Any]:
    store = Store(cfg.db_path)
    now = datetime.now(UTC)
    snap = store.latest_snapshot("claude")
    decision = store.latest_decision()
    windows = (
        {
            name: {"used_pct": w.used_pct, "resets_at": w.resets_at.isoformat()}
            for name, w in snap.windows.items()
        }
        if snap
        else {}
    )
    week_pct = 0.0
    if snap and SEVEN_DAY in snap.windows:
        week_start = snap.windows[SEVEN_DAY].resets_at - timedelta(days=7)
        coef = (decision.detail.get("token_coef") if decision else None) or INITIAL_COEF
        week_pct = store.harness_tokens_between(week_start, now) * coef
    return {
        "observed_at": snap.observed_at.isoformat() if snap else None,
        "windows": windows,
        "decision": (
            {
                "at": decision.at.isoformat(),
                "parallel": decision.parallel,
                "reason": decision.reason,
                "detail": dict(decision.detail),
            }
            if decision
            else None
        ),
        "jobs": [j for j in store.list_jobs() if j["state"] in ("running", "queued")],
        "harness_week_pct": week_pct,
        "kill_switch": (cfg.state_dir / KILL_FILENAME).exists(),
    }


def _print_status(data: dict[str, Any]) -> None:
    if not data["windows"]:
        print("no observation yet")
    else:
        print(f"observed {data['observed_at']}")
        for name, w in data["windows"].items():
            print(f"  {name}: {w['used_pct']:.1f}% (resets {w['resets_at']})")
    d = data["decision"]
    print(f"decision: parallel={d['parallel']} ({d['reason']}) at {d['at']}" if d else "decision: none")
    print(f"harness this week: {data['harness_week_pct']:.2f}%")
    if data["kill_switch"]:
        print("KILL SWITCH ON")
    for j in data["jobs"]:
        print(f"  job {j['job_id']} {j['state']} pid={j['pid']}: {' '.join(j['args'])}")


def _format_line(payload: str) -> str:
    try:
        limits = json.loads(payload).get("rate_limits") or {}
    except (json.JSONDecodeError, AttributeError):
        return ""
    parts = [
        f"{label} {limits[name]['used_percentage']:g}%"
        for name, label in ((FIVE_HOUR, "5h"), (SEVEN_DAY, "7d"))
        if isinstance(limits.get(name), dict) and "used_percentage" in limits[name]
    ]
    return " · ".join(parts)


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="aqs", description="Leftover-quota scheduler for harness2")
    sub = p.add_subparsers(dest="cmd", required=True)

    def with_config(sp: argparse.ArgumentParser) -> argparse.ArgumentParser:
        sp.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
        return sp

    with_config(sub.add_parser("daemon", help="run the control loop"))
    st = with_config(sub.add_parser("status", help="show quota, decision and jobs"))
    st.add_argument("--json", action="store_true")
    q = sub.add_parser("queue", help="job queue")
    qsub = q.add_subparsers(dest="queue_cmd", required=True)
    add = with_config(qsub.add_parser("add", help="enqueue a harness2 run: aqs queue add -- ARGS"))
    add.add_argument("--id", dest="job_id")
    add.add_argument("args", nargs=argparse.REMAINDER)
    with_config(qsub.add_parser("list"))
    with_config(sub.add_parser("kill", help="stop the harness (kill switch on)"))
    with_config(sub.add_parser("unkill", help="kill switch off"))
    hook = sub.add_parser("statusline-hook", help="Claude Code statusline command")
    hook.add_argument("--out", type=Path, required=True)
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.cmd == "statusline-hook":
        payload = sys.stdin.read()
        statusline_hook(payload, args.out)
        print(_format_line(payload))
        return 0
    cfg = load_cli_config(args.config)
    if args.cmd == "daemon":
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
        run_forever(build_context(cfg))
        return 0
    if args.cmd == "status":
        data = _status(cfg)
        if args.json:
            print(json.dumps(data, ensure_ascii=False))
        else:
            _print_status(data)
        return 0
    if args.cmd == "queue":
        store = Store(cfg.db_path)
        if args.queue_cmd == "add":
            extra = args.args[1:] if args.args[:1] == ["--"] else args.args
            job_id = args.job_id or datetime.now(UTC).strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
            store.enqueue(job_id, extra)
            print(job_id)
        else:
            for j in store.list_jobs():
                print(f"{j['job_id']}\t{j['state']}\t{' '.join(j['args'])}")
        return 0
    kill_file = cfg.state_dir / KILL_FILENAME
    if args.cmd == "kill":
        kill_file.parent.mkdir(parents=True, exist_ok=True)
        kill_file.touch()
        print(f"kill switch on: {kill_file}")
    else:
        kill_file.unlink(missing_ok=True)
        print("kill switch off")
    return 0
