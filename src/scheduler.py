"""IST corpus schedules. The API process runs ingest itself.

Same behavior locally and on Railway: at a saved IST time this process
runs ingest.refresh_corpus, then rebuilds Chroma so chat uses the new
pages. GitHub Actions is optional (SCHEDULER_BACKEND=github). Tests skip
the background loop.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
IST = ZoneInfo("Asia/Kolkata")
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
MAX_RUNS = 200
TICK_SECONDS = 15
_LOGGER = logging.getLogger("scheduler")
_LOCK = threading.RLock()
_JOB_LOCK = threading.Lock()
_LOOP_STOP: threading.Event | None = None
_LOOP_THREAD: threading.Thread | None = None

ExecuteFn = Callable[[], tuple[bool, str]]


class SchedulerError(ValueError):
    """Bad schedule payload."""


def store_dir() -> Path:
    raw = os.environ.get("SCHEDULER_DIR", "").strip()
    return Path(raw) if raw else ROOT / "data" / "scheduler"


def schedules_path() -> Path:
    return store_dir() / "schedules.json"


def runs_path() -> Path:
    return store_dir() / "runs.jsonl"


def claimed_path() -> Path:
    return store_dir() / "claimed.json"


def should_run_loop() -> bool:
    flag = os.environ.get("SKIP_SCHEDULER_LOOP", "").strip().lower()
    if flag in {"1", "true", "yes"}:
        return False
    if flag in {"0", "false", "no"}:
        return True
    if any(name == "tests" or name.startswith("tests.") for name in sys.modules):
        return False
    if "pytest" in sys.modules:
        return False
    return True


def now_ist(moment: datetime | None = None) -> datetime:
    if moment is None:
        return datetime.now(IST)
    if moment.tzinfo is None:
        return moment.replace(tzinfo=IST)
    return moment.astimezone(IST)


def iso(moment: datetime) -> str:
    return now_ist(moment).isoformat(timespec="seconds")


def parse_times(raw: object) -> list[str]:
    if isinstance(raw, str):
        values = [raw]
    elif isinstance(raw, list):
        values = raw
    else:
        raise SchedulerError("times must be a list of HH:MM values")
    times: list[str] = []
    for item in values:
        text = str(item).strip()
        if re.match(r"^([01]\d|2[0-3]):[0-5]\d:[0-5]\d$", text):
            text = text[:5]
        if not TIME_RE.match(text):
            raise SchedulerError(f"invalid time {text!r}; use HH:MM in 24-hour IST")
        if text not in times:
            times.append(text)
    if not times:
        raise SchedulerError("add at least one IST time")
    return sorted(times)


def _read_json(path: Path, fallback: Any) -> Any:
    if not path.is_file():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return fallback


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def load_schedules() -> list[dict[str, Any]]:
    with _LOCK:
        data = _read_json(schedules_path(), {"schedules": []})
    rows = data.get("schedules") if isinstance(data, dict) else []
    if not isinstance(rows, list):
        return []
    return [item for item in rows if isinstance(item, dict) and item.get("id")]


def _save_schedules(rows: list[dict[str, Any]]) -> None:
    _write_json(schedules_path(), {"schedules": rows})


def load_runs(limit: int = 50, schedule_id: str | None = None) -> list[dict[str, Any]]:
    path = runs_path()
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(item, dict):
            continue
        if schedule_id and item.get("schedule_id") != schedule_id:
            continue
        rows.append(item)
    rows.reverse()
    return rows[: max(1, min(limit, MAX_RUNS))]


def _append_run(row: dict[str, Any]) -> None:
    path = runs_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row) + "\n")


def _claimed() -> set[str]:
    data = _read_json(claimed_path(), [])
    if not isinstance(data, list):
        return set()
    return {str(item) for item in data}


def _save_claimed(slots: set[str]) -> None:
    kept = sorted(slot for slot in slots if slot.count(":") >= 2)
    _write_json(claimed_path(), kept[-500:])


def slot_claimed(slot: str) -> bool:
    with _LOCK:
        return slot in _claimed()


def claim_slot(slot: str) -> bool:
    with _LOCK:
        slots = _claimed()
        if slot in slots:
            return False
        slots.add(slot)
        _save_claimed(slots)
        return True


def next_run_at(times: list[str], moment: datetime | None = None) -> str | None:
    if not times:
        return None
    current = now_ist(moment)
    today = current.date()
    candidates: list[datetime] = []
    for stamp in times:
        hour, minute = (int(part) for part in stamp.split(":"))
        candidate = datetime(today.year, today.month, today.day, hour, minute, tzinfo=IST)
        if candidate <= current:
            candidate = candidate + timedelta(days=1)
        candidates.append(candidate)
    return iso(min(candidates))


def public_schedule(row: dict[str, Any], moment: datetime | None = None) -> dict[str, Any]:
    times = list(row.get("times") or [])
    enabled = bool(row.get("enabled"))
    return {
        "id": row["id"],
        "name": row.get("name") or "Corpus refresh",
        "times": times,
        "timezone": "Asia/Kolkata",
        "enabled": enabled,
        "paused": not enabled,
        "action": "refresh_corpus",
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        "last_run_at": row.get("last_run_at"),
        "last_status": row.get("last_status"),
        "next_run_at": next_run_at(times, moment) if enabled else None,
    }


def create_schedule(name: str, times: object) -> dict[str, Any]:
    stamp = iso(now_ist())
    row = {
        "id": str(uuid.uuid4()),
        "name": (name or "Corpus refresh").strip()[:80] or "Corpus refresh",
        "times": parse_times(times),
        "enabled": True,
        "action": "refresh_corpus",
        "created_at": stamp,
        "updated_at": stamp,
        "last_run_at": None,
        "last_status": None,
    }
    with _LOCK:
        rows = load_schedules()
        rows.append(row)
        _save_schedules(rows)
    return public_schedule(row)


def get_schedule(schedule_id: str) -> dict[str, Any] | None:
    for row in load_schedules():
        if row["id"] == schedule_id:
            return row
    return None


def update_schedule(schedule_id: str, **changes: Any) -> dict[str, Any] | None:
    with _LOCK:
        rows = load_schedules()
        updated: dict[str, Any] | None = None
        for row in rows:
            if row["id"] != schedule_id:
                continue
            if "name" in changes and changes["name"] is not None:
                row["name"] = str(changes["name"]).strip()[:80] or row["name"]
            if "times" in changes and changes["times"] is not None:
                row["times"] = parse_times(changes["times"])
            if "enabled" in changes and changes["enabled"] is not None:
                row["enabled"] = bool(changes["enabled"])
            row["updated_at"] = iso(now_ist())
            updated = row
            break
        if updated is None:
            return None
        _save_schedules(rows)
    return public_schedule(updated)


def delete_schedule(schedule_id: str) -> bool:
    with _LOCK:
        rows = load_schedules()
        kept = [row for row in rows if row["id"] != schedule_id]
        if len(kept) == len(rows):
            return False
        _save_schedules(kept)
        return True


def _patch_run(run_id: str, **fields: Any) -> dict[str, Any] | None:
    path = runs_path()
    if not path.is_file():
        return None
    found: dict[str, Any] | None = None
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(item, dict):
            continue
        if item.get("id") == run_id:
            item.update(fields)
            found = item
        rows.append(item)
    path.write_text("".join(json.dumps(item) + "\n" for item in rows), encoding="utf-8")
    return found


def _set_schedule_result(schedule_id: str, status: str, when: str) -> None:
    with _LOCK:
        rows = load_schedules()
        for row in rows:
            if row["id"] == schedule_id:
                row["last_run_at"] = when
                row["last_status"] = status
                row["updated_at"] = when
                break
        _save_schedules(rows)


def github_token() -> str:
    return (
        os.environ.get("SCHEDULER_GITHUB_TOKEN", "").strip()
        or os.environ.get("GITHUB_TOKEN", "").strip()
    )


def github_repo() -> str:
    return os.environ.get("SCHEDULER_GITHUB_REPO", "manisdevs-git/mfchatbot").strip()


def github_ref() -> str:
    return os.environ.get("SCHEDULER_GITHUB_REF", "main").strip() or "main"


def scheduler_backend() -> str:
    raw = os.environ.get("SCHEDULER_BACKEND", "local").strip().lower()
    if raw in {"github", "action", "actions"}:
        return "github"
    return "local"


def _github_request(method: str, url: str, token: str, payload: dict | None = None) -> Any:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(
        url,
        data=body,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "mfchatbot-scheduler",
            "Content-Type": "application/json",
        },
    )
    try:
        with urlopen(request, timeout=45) as response:
            raw = response.read()
            if not raw:
                return {}
            return json.loads(raw.decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise SchedulerError(f"GitHub API {exc.code} for {method} {url}: {detail}") from exc
    except URLError as exc:
        raise SchedulerError(f"GitHub API unreachable: {exc.reason}") from exc


def local_ingest() -> tuple[bool, str]:
    timeout = int(os.environ.get("SCHEDULER_INGEST_TIMEOUT", "3600"))
    proc = subprocess.run(
        [sys.executable, "-m", "ingest.refresh_corpus"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    output = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
    tail = output[-1800:] if output else f"exit {proc.returncode}"
    if proc.returncode != 0:
        return False, tail
    try:
        from ingest.embed_index import ensure_persisted_index

        ready = ensure_persisted_index()
    except Exception as exc:
        return False, f"{tail}\nChroma rebuild failed: {exc}"[-2000:]
    note = "Chroma rebuilt from the new embeddings." if ready else "Ingest finished but Chroma is empty."
    return True, f"{tail}\n{note}"[-2000:]


def dispatch_github_ingest() -> tuple[bool, str]:
    """Start Refresh Groww corpus on GitHub. Checkout is origin/main, not local jsonl."""
    token = github_token()
    if not token:
        raise SchedulerError(
            "Set SCHEDULER_GITHUB_TOKEN (Actions: write) so the scheduler can start "
            "Refresh Groww corpus on GitHub. Ingest uses remote main, not local files."
        )
    repo = github_repo()
    ref = github_ref()
    workflow = os.environ.get("SCHEDULER_GITHUB_WORKFLOW", "refresh-corpus.yml").strip()
    base = f"https://api.github.com/repos/{repo}"
    dispatched_at = datetime.now(timezone.utc) - timedelta(seconds=20)
    _github_request(
        "POST",
        f"{base}/actions/workflows/{workflow}/dispatches",
        token,
        {"ref": ref, "inputs": {"dry_run": "false"}},
    )
    timeout = int(os.environ.get("SCHEDULER_INGEST_TIMEOUT", "3600"))
    deadline = time.time() + timeout
    last_url = ""
    while time.time() < deadline:
        payload = _github_request(
            "GET",
            f"{base}/actions/workflows/{workflow}/runs?event=workflow_dispatch&per_page=5",
            token,
        )
        runs = payload.get("workflow_runs") if isinstance(payload, dict) else None
        if isinstance(runs, list):
            for item in runs:
                if not isinstance(item, dict):
                    continue
                created_raw = str(item.get("created_at") or "")
                try:
                    created = datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
                except ValueError:
                    continue
                if created < dispatched_at:
                    continue
                last_url = str(item.get("html_url") or "")
                if str(item.get("status") or "") != "completed":
                    break
                conclusion = str(item.get("conclusion") or "unknown")
                sha = str(item.get("head_sha") or "")[:7]
                ok = conclusion == "success"
                return ok, (
                    f"GitHub Action {conclusion} on {ref} ({sha}). "
                    f"Ingest used remote branch files. {last_url}"
                ).strip()
        time.sleep(8)
    return False, f"GitHub Action on {ref} did not finish in time. {last_url}".strip()


def default_execute() -> tuple[bool, str]:
    if scheduler_backend() == "local":
        return local_ingest()
    return dispatch_github_ingest()


def _complete_run(run: dict[str, Any], execute: ExecuteFn) -> None:
    started = datetime.now(IST)
    if not _JOB_LOCK.acquire(blocking=False):
        finished = iso(now_ist())
        _patch_run(
            run["id"],
            status="skipped",
            finished_at=finished,
            duration_ms=0,
            result="Another corpus refresh is already running.",
        )
        _set_schedule_result(run["schedule_id"], "skipped", finished)
        return
    try:
        ok, detail = execute()
        finished_dt = datetime.now(IST)
        status = "success" if ok else "failure"
        _patch_run(
            run["id"],
            status=status,
            finished_at=iso(finished_dt),
            duration_ms=int((finished_dt - started).total_seconds() * 1000),
            result=detail[:2000],
        )
        _set_schedule_result(run["schedule_id"], status, iso(finished_dt))
    except Exception as exc:
        finished = iso(now_ist())
        _LOGGER.exception("scheduled ingest failed")
        _patch_run(
            run["id"],
            status="failure",
            finished_at=finished,
            duration_ms=int((datetime.now(IST) - started).total_seconds() * 1000),
            result=str(exc)[:2000],
        )
        _set_schedule_result(run["schedule_id"], "failure", finished)
    finally:
        _JOB_LOCK.release()


def _spawn(run: dict[str, Any], execute: ExecuteFn | None) -> None:
    worker = execute or default_execute

    def target() -> None:
        _complete_run(run, worker)

    if os.environ.get("SCHEDULER_INLINE", "").strip() in {"1", "true", "yes"}:
        target()
        return
    threading.Thread(target=target, name="corpus-schedule", daemon=True).start()


def _new_run(schedule: dict[str, Any], slot: str, trigger: str) -> dict[str, Any]:
    stamp = iso(now_ist())
    run = {
        "id": str(uuid.uuid4()),
        "schedule_id": schedule["id"],
        "schedule_name": schedule.get("name") or "Corpus refresh",
        "slot": slot,
        "trigger": trigger,
        "status": "running",
        "started_at": stamp,
        "finished_at": None,
        "duration_ms": None,
        "result": "Corpus refresh started.",
        "action": "refresh_corpus",
    }
    with _LOCK:
        _append_run(run)
    return run


def start_run(schedule_id: str, *, trigger: str = "manual", execute: ExecuteFn | None = None) -> dict[str, Any]:
    row = get_schedule(schedule_id)
    if row is None:
        raise SchedulerError("schedule not found")
    slot = f"{schedule_id}:{trigger}:{uuid.uuid4()}"
    run = _new_run(row, slot, trigger)
    _spawn(run, execute)
    return run


def fire_due_schedules(moment: datetime | None = None, *, execute: ExecuteFn | None = None) -> list[dict[str, Any]]:
    current = now_ist(moment)
    hhmm = current.strftime("%H:%M")
    day = current.date().isoformat()
    started: list[dict[str, Any]] = []
    for row in load_schedules():
        if not row.get("enabled"):
            continue
        times = row.get("times") or []
        if hhmm not in times:
            continue
        slot = f"{row['id']}:{day}:{hhmm}"
        if not claim_slot(slot):
            continue
        run = _new_run(row, slot, "schedule")
        _spawn(run, execute)
        started.append(run)
    return started


def _loop(stop: threading.Event) -> None:
    while not stop.wait(TICK_SECONDS):
        try:
            fire_due_schedules()
        except Exception:
            _LOGGER.exception("scheduler tick failed")


def start_loop() -> None:
    global _LOOP_STOP, _LOOP_THREAD
    if not should_run_loop():
        return
    if _LOOP_THREAD is not None and _LOOP_THREAD.is_alive():
        return
    store_dir().mkdir(parents=True, exist_ok=True)
    _LOOP_STOP = threading.Event()
    _LOOP_THREAD = threading.Thread(target=_loop, args=(_LOOP_STOP,), name="corpus-scheduler", daemon=True)
    _LOOP_THREAD.start()
    _LOGGER.info("corpus scheduler loop started (IST times)")


def stop_loop() -> None:
    global _LOOP_STOP, _LOOP_THREAD
    if _LOOP_STOP is not None:
        _LOOP_STOP.set()
    _LOOP_STOP = None
    _LOOP_THREAD = None
