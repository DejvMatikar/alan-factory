from __future__ import annotations

import contextlib
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from alan import worker_records
from alan.claude_cli_journal import (
    AttachSpec,
    PidHandle,
    StreamEvent,
    journal_err_path,
    tail_to_result,
)


class _RecordingConsumer:
    def handle(self, line: str, events: list[tuple[str, StreamEvent]]) -> None:
        events.append((line, json.loads(line)))


def test_detached_child_survives_spawner_group_kill_and_fresh_reader_tails_to_terminal(
    tmp_path: Path,
) -> None:
    journal = tmp_path / "stream.jsonl"
    err = journal_err_path(journal)
    marker = tmp_path / "child_pid.txt"
    journal.touch()
    err.touch()

    child_script = (
        "import json, time, pathlib\n"
        f"p = pathlib.Path({str(journal)!r})\n"
        "for i in range(5):\n"
        "    with p.open('a') as f:\n"
        "        f.write(json.dumps({'type': 'progress', 'i': i}) + chr(10))\n"
        "    time.sleep(0.2)\n"
        "with p.open('a') as f:\n"
        "    f.write(json.dumps({'type': 'result', 'subtype': 'success', "
        "'result': 'survived'}) + chr(10))\n"
    )
    service_script = (
        "import subprocess, sys\n"
        f"child = subprocess.Popen([sys.executable, '-c', {child_script!r}], "
        "start_new_session=True)\n"
        f"open({str(marker)!r}, 'w').write(str(child.pid))\n"
        "import time as _t; _t.sleep(30)\n"
    )
    service = subprocess.Popen(
        [sys.executable, "-c", service_script], start_new_session=True,
    )
    child_pid: int | None = None
    try:
        for _ in range(50):
            if marker.exists():
                break
            time.sleep(0.05)
        assert marker.exists(), "service never spawned its detached child"
        child_pid = int(marker.read_text())

        os.killpg(os.getpgid(service.pid), signal.SIGTERM)
        service.wait(timeout=5.0)

        identity = None
        for _ in range(50):
            identity = worker_records.ps_identity(child_pid)
            if identity is not None:
                break
            time.sleep(0.05)
        assert identity is not None, "detached child did not survive the spawner group kill"
        lstart, cmdline = identity

        record = worker_records.WorkerRecord(
            schema_version=worker_records.RECORD_VERSION,
            job_id=None, task_id="t1", workspace_id="ws-1", team="research",
            inv_id="inv_001", node_id="impl", role="impl", model="sonnet",
            engine="default", slot_path=str(tmp_path), session_id="sess-survival",
            output_path=str(journal), pid=child_pid, started_at=1.0,
            lstart=lstart, cmdline=cmdline, deadline_at=None, idle_timeout_s=None,
            max_turns=150, service_pid=1, service_started_at="Thu Jan  1 00:00:00 1970",
            host=worker_records.current_host(), pgid=os.getpgid(child_pid),
            journal_offset=0, err_offset=0,
        )
        spec = AttachSpec(
            journal_path=journal, err_path=err, journal_offset=0, err_offset=0,
            idle_timeout=10.0, abs_timeout=30.0,
            idle_start=time.monotonic(), abs_start=time.monotonic(),
        )
        handle = PidHandle(record=record, this_service=(2, "Thu Jan  2 00:00:00 1970"))
        events: list[tuple[str, StreamEvent]] = []
        outcome = tail_to_result(
            spec, handle, events, _RecordingConsumer(), kill=lambda h: None, poll_interval=0.05,
        )
        assert outcome.pump.timed_out is False
        kinds = [e.get("type") for _, e in outcome.events]
        assert "result" in kinds
        results = [e for _, e in outcome.events if e.get("type") == "result"]
        assert results[-1]["result"] == "survived"
    finally:
        if child_pid is not None:
            with contextlib.suppress(ProcessLookupError):
                os.kill(child_pid, signal.SIGKILL)
        if service.poll() is None:
            service.kill()
        service.wait()
