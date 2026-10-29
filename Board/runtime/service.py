"""Stable start/status/stop entry point; signal processing remains in the existing ELF files."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / "runs"
MANAGER = Path(__file__).with_name("run_board.py")


def current():
    link = RUNS / "current"
    if not link.exists():
        return None, None
    run = link.resolve()
    if run.parent != RUNS.resolve():
        raise RuntimeError("Invalid current-session link")
    return run, json.loads((run / "session.json").read_text())


def running(session):
    if not session:
        return False
    try:
        command = Path("/proc", str(session["manager_pid"]), "cmdline").read_bytes()
    except FileNotFoundError:
        return False
    return str(MANAGER).encode() in command


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("start", "run", "stop", "status"))
    action = parser.parse_args().action
    run, session = current()
    active = running(session)
    if action == "status":
        print(json.dumps({"running": active, "session": str(run) if run else None,
                          "manager_pid": session["manager_pid"] if session else None,
                          "state": session.get("state", "running") if active else "stopped"},
                         indent=2))
        return 0
    if action == "stop":
        if active:
            (run / "STOP").touch()
            for _ in range(600):
                if not running(session):
                    print("Stopped " + str(run))
                    return 0
                time.sleep(0.2)
            raise RuntimeError("Stop timed out; inspect " + str(run))
        else:
            print("No active session.")
        return 0
    if active:
        raise RuntimeError("Already running: " + str(run))
    job = json.loads((ROOT / "config/job.json").read_text())
    index = 1
    while ((RUNS / ("moving_session_%03d" % index)).exists()
           or (ROOT / "logs" / ("moving_session_%03d.log" % index)).exists()):
        index += 1
    job.update(tag="moving_session_%03d" % index, project_root=str(ROOT),
               runtime_base=str(ROOT), bin_dir=str(ROOT / "bin"), results_subdir="runs")
    job_path = ROOT / "logs" / (job["tag"] + ".job.json")
    log_path = ROOT / "logs" / (job["tag"] + ".log")
    with log_path.open("xb") as log:
        job_path.write_text(json.dumps(job, indent=2))
        child = subprocess.Popen(
            [str(ROOT / "python.sh"), str(MANAGER), str(job_path)],
            cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
            start_new_session=action != "run")
    expected = RUNS / job["tag"]
    # [启动修正] 首次PL加载也在管理进程内完成，等待时间与启动服务一致。
    for _ in range(450):
        if (expected / "session.json").exists():
            temporary = RUNS / ".current"
            temporary.symlink_to(expected.name)
            os.replace(temporary, RUNS / "current")
            state = json.loads((expected / "session.json").read_text()).get("state", "running")
            prefix = {"waiting_for_source": "WAITING_FOR_SOURCE ", "waiting_for_pc": "WAITING_FOR_PC "}.get(state, "RUNNING ")
            print(prefix + str(expected))
            print("LOG " + str(log_path))
            return child.wait() if action == "run" else 0
        if child.poll() is not None:
            raise RuntimeError("Startup failed; see " + str(log_path))
        time.sleep(0.1)
    raise RuntimeError("Startup not yet confirmed; inspect " + str(log_path)
                       + " before trying again.")


if __name__ == "__main__":
    raise SystemExit(main())
