#!/usr/bin/env python3
"""
Pipeline run status, reconstructed from a run log (logs/pipeline_YYYYMMDD_HHMMSS.log).

Everything comes from what run_pipeline.sh and the steps already print: step
headers with timestamps, FAILED / skipped lines, and each step's summary lines.
So it works for any past run as well as the one in progress.

Usage (inspect a log):
    python scripts/run_status.py logs/pipeline_20261109_074703.log
"""

from __future__ import annotations

import html
import re
import statistics
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

SCHEDULED = (7, 0)          # launchd StartCalendarInterval
LATE_MIN = 5                # minutes after schedule before flagging a late start
SLOW_FACTOR = 1.5           # step slower than this x median of previous runs
HISTORY_RUNS = 7

_HEADER = re.compile(r"^\[(\d\d:\d\d:\d\d)\] === (.+?) ===\s*$")
_START = re.compile(r"^\[(\d\d:\d\d:\d\d)\] JobTracker daily pipeline starting")
_FAILED = re.compile(r"^\[\d\d:\d\d:\d\d\] FAILED: (.+)$")
_SKIPPED = re.compile(r"^\[\d\d:\d\d:\d\d\] (.+?) skipped\b")
_SOURCE = re.compile(r"^\[(\w+)\] Fetching")
_TOTAL = re.compile(r"^\s+Total jobs\s*:\s*(\d+)")
_INGEST = re.compile(
    r"^\s+Ingest\s*:\s*Ingested: (\d+) \| Enriched: \d+ \| Duplicates skipped: (\d+).*\| Errors: (\d+)"
)
_RETRY = re.compile(r"Retrying\b.*?host='([^']+)'")
_DNS = re.compile(r"NameResolutionError.*?host='([^']+)'")
_BG_DONE = re.compile(r"\[ollama-bg\] Complete\. Scored: (\d+) \| Errors: (\d+)")
_START_SCORING = re.compile(r"Starting scoring: (\d+) posts, engine=(\S+)")
_SCORED = re.compile(r"^Scored: (\d+) \| Skipped \(already scored\): \d+ \| Errors: (\d+)")
_CHECKED = re.compile(r"^Checked (\d+) open post\(s\) across \d+ source\(s\); (\d+) marked closed")
_NO_CHECK = re.compile(r"^\s+\[(\w+)\] no closure check available -- skipping (\d+) post")
_NOT_REG = re.compile(r"^\s+\[(\w+)\] skipping (.+?): not in confirmed registry")
_RERANK_MODEL = re.compile(r"^Model : (\S+)")
_RANKED = re.compile(r"^Ranked: (\d+) new posts \| Errors: (\d+)")
_RERANK_SKIP = re.compile(r"^Skipped: (.+)$")
_ERR_ITEM = re.compile(r"^\s+- (.+)$")
_ATTEMPT1 = re.compile(r"rank call attempt 1 failed")
_REPORT_SENT = re.compile(r"^Report sent to (\S+)")

SOURCE_LABELS = {"jobspy": "LinkedIn", "greenhouse": "Greenhouse", "comeet": "Comeet",
                 "ashby": "Ashby", "lever": "Lever"}

# Header label in the log -> display name
STEP_NAMES = {
    "Ingest": "Ingest",
    "Closed-posting check": "Closed check",
    "Rules scoring": "Rules scoring",
    "Ollama upgrade": "Llama scoring",
    "Reranker": "Reranker",
    "Daily report": "Daily report",
}


@dataclass
class Step:
    name: str
    started: datetime
    duration: timedelta | None = None
    status: str = "OK"              # OK | FAILED | SKIPPED | RUNNING
    result: str = ""
    details: list[str] = field(default_factory=list)   # indented sub-lines
    lines: list[str] = field(default_factory=list)


@dataclass
class RunStatus:
    log_path: Path
    started: datetime | None = None
    finished: datetime | None = None
    steps: list[Step] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)

    @property
    def failed(self) -> list[Step]:
        return [s for s in self.steps if s.status == "FAILED"]


def _run_date(log_path: Path) -> datetime:
    m = re.search(r"pipeline_(\d{8})_\d{6}", log_path.name)
    return datetime.strptime(m.group(1), "%Y%m%d") if m else datetime.now()


def _at(day: datetime, hms: str) -> datetime:
    t = datetime.strptime(hms, "%H:%M:%S")
    return day.replace(hour=t.hour, minute=t.minute, second=t.second)


def _fmt_dur(d: timedelta | None) -> str:
    if d is None:
        return "-"
    s = int(d.total_seconds())
    if s < 1:
        return "<1s"
    if s < 60:
        return f"{s}s"
    h, rem = divmod(s, 3600)
    m = rem // 60
    return f"{h}h{m:02d}m" if h else f"{m}m"


def _short_error(msg: str) -> str:
    msg = re.sub(r"^Window failed: ", "", msg)
    msg = re.sub(r"^rank call failed after \d+ attempts: ", "", msg)
    if "No such file or directory: 'claude'" in msg:
        return "claude: command not found"
    return msg[:110]


def _split_steps(log_path: Path) -> RunStatus:
    day = _run_date(log_path)
    run = RunStatus(log_path=log_path)
    current: Step | None = None
    for raw in log_path.read_text(errors="replace").splitlines():
        if run.started is None and (m := _START.match(raw)):
            run.started = _at(day, m.group(1))
            continue
        if m := _HEADER.match(raw):
            ts = _at(day, m.group(1))
            if current is not None:
                current.duration = ts - current.started
            label = m.group(2).replace(" (best-effort)", "")
            if label.startswith("Pipeline complete"):
                run.finished = ts
                current = None
                continue
            current = Step(name=STEP_NAMES.get(label, label), started=ts)
            run.steps.append(current)
            continue
        if current is not None:
            current.lines.append(raw)
    if run.steps and run.steps[-1].duration is None:
        run.steps[-1].status = "RUNNING"
    return run


def _fill_ingest(step: Step) -> None:
    sources: list[tuple[str, int, int, int, int]] = []
    src = None
    total = None
    retries: dict[str, int] = {}
    dns: dict[str, int] = {}
    for line in step.lines:
        if m := _SOURCE.match(line):
            src, total = SOURCE_LABELS.get(m.group(1), m.group(1)), None
        elif (m := _TOTAL.match(line)) and src:
            total = int(m.group(1))
        elif (m := _INGEST.match(line)) and src:
            sources.append((src, total or 0, int(m.group(1)), int(m.group(2)), int(m.group(3))))
            src = None
        if m := _RETRY.search(line):
            retries[m.group(1)] = retries.get(m.group(1), 0) + 1
        if m := _DNS.search(line):
            dns[m.group(1)] = dns.get(m.group(1), 0) + 1
    fetched = sum(s[1] for s in sources)
    new = sum(s[2] for s in sources)
    errors = sum(s[4] for s in sources)
    step.result = f"{fetched:,} fetched - {new} new - {errors} errors"
    if errors and step.status == "OK":
        step.status = "FAILED"
    for name, f, n, d, e in sorted(sources, key=lambda s: -s[1]):
        line = f"{name}: {f:,} fetched - {n} new - {d:,} dupes"
        step.details.append(line + (f" - {e} errors" if e else ""))
    if not sources:
        step.details.append("no per-source summary found in log")
    for host, n in sorted(retries.items()):
        extra = f", {dns[host]} DNS failures" if host in dns else ""
        step.details.append(f"network: {n} retried timeouts on {host}{extra}")


def _fill_closed(step: Step) -> None:
    for line in step.lines:
        if m := _CHECKED.match(line):
            step.result = f"{int(m.group(1)):,} checked - {m.group(2)} closed"
        elif m := _NO_CHECK.match(line):
            step.details.append(
                f"not checkable: {SOURCE_LABELS.get(m.group(1), m.group(1))} ({m.group(2)} posts)")
    skipped = [m.group(2) for line in step.lines if (m := _NOT_REG.match(line))]
    if skipped:
        step.details.append("not in registry, not checked: " + ", ".join(skipped))


def _fill_scoring(step: Step, bg_scored: int | None = None) -> None:
    pending = engine = None
    scored = errs = 0
    for line in step.lines:
        if m := _START_SCORING.search(line):
            pending, engine = int(m.group(1)), m.group(2)
        elif m := _SCORED.match(line):
            scored, errs = int(m.group(1)), int(m.group(2))
    if pending is None:
        if step.status == "OK":
            step.result = "no summary in log"
        return
    left = pending - scored
    parts = []
    if bg_scored is not None:
        parts.append(f"{bg_scored} scored during ingest")
    if pending:
        parts.append(f"{scored} scored")
    if left or errs:
        parts.append(f"{left} left, {errs} errors")
    else:
        parts.append("0 left" if (pending or bg_scored is not None) else "nothing pending")
    step.result = " - ".join(parts)
    if engine and step.name == "Llama scoring":
        step.details.append(f"engine: {engine}")
    if errs and step.status == "OK":
        step.status = "FAILED"


def _fill_reranker(step: Step) -> None:
    model = None
    first_err = None
    in_errors = False
    for line in step.lines:
        if m := _RERANK_MODEL.match(line):
            model = m.group(1)
        elif m := _RANKED.match(line):
            ranked, errs = int(m.group(1)), int(m.group(2))
            first_tries = sum(1 for l in step.lines if _ATTEMPT1.search(l))
            recovered = max(first_tries - errs, 0)
            step.result = f"{ranked} ranked - {errs} batch{"es" if errs != 1 else ""} failed"
            if recovered:
                step.result += f" ({recovered} recovered on retry)"
            if errs:
                step.status = "FAILED"
        elif m := _RERANK_SKIP.match(line):
            step.result = "nothing new to rank"
        elif line.startswith("Errors:"):
            in_errors = True
        elif in_errors and first_err is None and (m := _ERR_ITEM.match(line)):
            first_err = _short_error(m.group(1))
    if first_err:
        step.details.append(f"first error: {first_err}")
    if model:
        step.details.append(f"model: {model}")


def _fill_report(step: Step) -> None:
    for line in step.lines:
        if m := _REPORT_SENT.match(line):
            step.result = f"sent to {m.group(1)}"
    if step.status == "RUNNING":
        step.status, step.result = "OK", "sending"


def parse_run_log(log_path: str | Path, live: bool = False) -> RunStatus:
    """live=True: called from inside the run, so the last step is the report itself."""
    run = _split_steps(Path(log_path))
    all_lines = [l for s in run.steps for l in s.lines]
    failed = {m.group(1) for l in all_lines if (m := _FAILED.match(l))}
    skipped = {m.group(1) for l in all_lines if (m := _SKIPPED.match(l))}
    bg = [int(m.group(1)) for l in all_lines if (m := _BG_DONE.search(l))]
    for s in run.steps:
        raw_label = next((k for k, v in STEP_NAMES.items() if v == s.name), s.name)
        if any(f.startswith(raw_label) for f in failed):
            s.status = "FAILED"
        elif any(k.startswith(raw_label) for k in skipped):
            s.status, s.result = "SKIPPED", "skipped (see log)"
        if s.name == "Ingest":
            _fill_ingest(s)
        elif s.name == "Closed check":
            _fill_closed(s)
        elif s.name == "Rules scoring":
            _fill_scoring(s)
        elif s.name == "Llama scoring":
            _fill_scoring(s, bg_scored=sum(bg) if bg else None)
        elif s.name == "Reranker":
            _fill_reranker(s)
        elif s.name == "Daily report":
            _fill_report(s)
    if not live and run.steps and run.steps[-1].status == "RUNNING":
        run.flags.append("run log ends mid-step: the run is still going or was interrupted")
    _add_flags(run)
    return run


def _previous_durations(log_path: Path) -> dict[str, list[float]]:
    logs = sorted(p for p in log_path.parent.glob("pipeline_*.log") if p.name < log_path.name)
    out: dict[str, list[float]] = {}
    for p in logs[-HISTORY_RUNS:]:
        for s in _split_steps(p).steps:
            if s.duration is not None:
                out.setdefault(s.name, []).append(s.duration.total_seconds())
    return out


def _add_flags(run: RunStatus) -> None:
    if run.started:
        sched = run.started.replace(hour=SCHEDULED[0], minute=SCHEDULED[1], second=0)
        late = (run.started - sched).total_seconds() / 60
        if LATE_MIN < late < 5 * 60:
            run.flags.append(
                f"started {run.started:%H:%M}, {int(late)} min after the scheduled "
                f"{sched:%H:%M} (Mac asleep, or a manual run)")
    history = _previous_durations(run.log_path)
    for s in run.steps:
        prev = history.get(s.name, [])
        if s.duration is None or len(prev) < 3:
            continue
        med = statistics.median(prev)
        if med >= 60 and s.duration.total_seconds() > SLOW_FACTOR * med:
            run.flags.append(
                f"{s.name} took {_fmt_dur(s.duration)}, vs a median of "
                f"{_fmt_dur(timedelta(seconds=med))} over the previous {len(prev)} runs")


# ── Rendering ──────────────────────────────────────────────────────────────────

_STATUS_STYLE = {"OK": "#1a7f37", "FAILED": "#cf222e", "SKIPPED": "#9a6700", "RUNNING": "#9a6700"}


def subject_marker(run: RunStatus) -> str:
    n = len(run.failed)
    return "✅" if n == 0 else f"⚠️ {n} step{'s' if n > 1 else ''} failed"


def render_text(run: RunStatus, db: dict | None = None) -> str:
    out = []
    end = f" - finished {run.finished:%H:%M}" if run.finished else ""
    total = f" - total {_fmt_dur(run.finished - run.started)}" if run.finished and run.started else ""
    out.append(f"Run: started {run.started:%H:%M}{end}{total}" if run.started else "Run: start unknown")
    out.append("")
    for s in run.steps:
        out.append(f"{s.name:<15} {s.status:<8} {_fmt_dur(s.duration):<7} {s.result}")
        out += [f"{'':<33}{d}" for d in s.details]
    if db:
        out += ["", _db_line(db)]
    if run.flags:
        out += ["", "Flags:"] + [f"  - {f}" for f in run.flags]
    return "\n".join(out)


def _db_line(db: dict) -> str:
    return (f"Pipeline: {db['open']:,} open - {db['top']} at 80+ - "
            f"{db['waiting']} of them waiting for a Claude rank")


def render_html(run: RunStatus, db: dict | None = None, note: str = "") -> str:
    h = html.escape
    td = "padding:4px 14px 4px 0;vertical-align:top"
    rows = ""
    for s in run.steps:
        color = _STATUS_STYLE.get(s.status, "#666")
        dur = "-" if (s.name == "Daily report" and s.result == "sending") else _fmt_dur(s.duration)
        rows += (
            f"<tr style='border-top:1px solid #e5e7eb'>"
            f"<td style='{td};font-weight:600;white-space:nowrap'>{h(s.name)}</td>"
            f"<td style='{td};color:{color};font-weight:700;white-space:nowrap'>{h(s.status)}</td>"
            f"<td style='{td};color:#555;white-space:nowrap'>{h(dur)}</td>"
            f"<td style='{td}'>{h(s.result)}"
        )
        for d in s.details:
            rows += f"<div style='font-size:12px;color:#666;margin-top:2px'>{h(d)}</div>"
        rows += "</td></tr>"

    head_parts = []
    if run.started:
        head_parts.append(f"Started {run.started:%H:%M}")
    if run.finished:
        head_parts.append(f"finished {run.finished:%H:%M}")
        if run.started:
            head_parts.append(f"total {_fmt_dur(run.finished - run.started)}")
    n_failed = len(run.failed)
    verdict = ("All steps OK" if n_failed == 0
               else f"{n_failed} step{'s' if n_failed > 1 else ''} failed: "
                    + ", ".join(s.name for s in run.failed))
    vcolor = "#1a7f37" if n_failed == 0 else "#cf222e"
    bg = "#f0fdf4" if n_failed == 0 else "#fff1f0"

    flags = ""
    if run.flags:
        flags = ("<div style='margin-top:10px;font-size:12px;color:#9a6700'>"
                 + "".join(f"<div>⚑ {h(f)}</div>" for f in run.flags) + "</div>")
    db_html = (f"<div style='margin-top:10px;font-size:13px'>{h(_db_line(db))}</div>" if db else "")
    note_html = f"<div style='margin-top:8px;font-size:11px;color:#888'>{h(note)}</div>" if note else ""

    return f"""
<div style="background:{bg};border:1px solid #e5e7eb;border-radius:6px;padding:12px 16px;margin-bottom:24px">
  <div style="font-weight:700;color:{vcolor};margin-bottom:2px">{h(verdict)}</div>
  <div style="font-size:12px;color:#555;margin-bottom:8px">{h(' - '.join(head_parts))}</div>
  <table style="border-collapse:collapse;font-size:13px;width:100%">{rows}</table>
  {db_html}{flags}{note_html}
</div>"""


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: run_status.py <pipeline log>")
    print(render_text(parse_run_log(sys.argv[1])))
