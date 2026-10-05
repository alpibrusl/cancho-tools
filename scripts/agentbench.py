#!/usr/bin/env python3
"""The agent benchmark, tools against plain bash (docs/agent-bench.md).

    python3 scripts/agentbench.py verify                       # §4: the checks can fail
    python3 scripts/agentbench.py run --agent opencode --model ollama/qwen3.8:27b-mlx --pilot
    python3 scripts/agentbench.py run --agent claude --model sonnet --arms mcp,skills
    python3 scripts/agentbench.py report bench-out/results.jsonl

`run` builds each task's workspace afresh, runs one agent headless in one arm,
checks the final workspace and the answer, and appends one JSON line. A run
already recorded is skipped, so an interrupted benchmark resumes. Nothing in a
person's own agent configuration reaches a run: opencode gets its own config,
data and cache directories, and Claude Code only the project's settings.
"""

import argparse
import json
import math
import os
import pathlib
import shutil
import signal
import statistics
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import agentbench_tasks as tasks  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
ARMS = ("bash", "mcp", "skills")
TOOL_NAMES = ["seek", "list", "peek", "jsonq", "tally", "hash", "write", "replace"]
PREFACE = "You are working in the current directory, which is the workspace. "
# What the harness's own tools are, so that an arm that is not `bash`
# cannot reach files any other way (docs/agent-bench.md §2).
CLAUDE_BUILTINS = ["Read", "Grep", "Glob", "Edit", "Write", "NotebookEdit", "WebFetch", "WebSearch", "Task", "TodoWrite"]


def bin_dir():
    return pathlib.Path(os.environ.get("TOOLBOX_BIN", ROOT / "build")).resolve()


# ---- workspaces and the reference scripts -------------------------------------


def new_workspace(task, base_dir=None):
    """A fresh run directory with the task's workspace in `ws`; answers
    `(run_dir, ws, facts)`."""
    run_dir = pathlib.Path(tempfile.mkdtemp(prefix="agentbench-", dir=base_dir))
    ws = run_dir / "ws"
    ws.mkdir()
    facts = {}
    task.build(ws, facts)
    return run_dir, ws, facts


def run_script(script, ws, facts, bins):
    p = subprocess.run(["bash", "-c", tasks.fill(script, facts)], cwd=ws, capture_output=True, text=True,
                       env={**os.environ, "BIN": str(bins)}, timeout=120)
    return p.stdout


def verify(bins):
    """§4, for every task: the reference solutions pass, doing nothing fails, and
    the unsafe solution fails. Answers a list of problems (empty when all hold)."""
    problems = []
    for t in tasks.TASKS:
        for kind in ("bash", "tools", "bad", "nothing"):
            script = {"bash": t.bash, "tools": t.tools, "bad": t.bad, "nothing": ""}[kind]
            if script is None or (kind == "bad" and not script):
                continue
            run_dir, ws, facts = new_workspace(t)
            try:
                answer = run_script(script, ws, facts, bins) if script else ""
                ok, why = t.check(ws, answer, facts)
            finally:
                shutil.rmtree(run_dir, ignore_errors=True)
            want = kind in ("bash", "tools")
            if ok != want:
                problems.append("%s: the `%s` solution %s the checker (%s)" % (t.id, kind, "failed" if want else "passed", why))
    return problems


# ---- the arms ---------------------------------------------------------------------


def skills_into(ws, where):
    for name in TOOL_NAMES:
        d = ws / where / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "SKILL.md").write_text(subprocess.run([str(bin_dir() / name), "skill"], capture_output=True, text=True,
                                                   check=True).stdout)


def mcp_server_command(ws):
    """The MCP server, confined to `ws`; built once into the cache."""
    exe = tasks.CACHE / "mcp"
    tasks.CACHE.mkdir(exist_ok=True)
    if not exe.exists() or exe.stat().st_mtime < (bin_dir() / "seek").stat().st_mtime:
        subprocess.run([sys.executable, str(ROOT / "scripts" / "mcp.py"), "build", "--bin", str(bin_dir()), "--out", str(exe),
                        "--build-dir", str(bin_dir())], check=True, capture_output=True)
    return [str(exe), "--root", str(ws)]


def tool_patterns():
    """Bash patterns for the eight binaries, by name and by absolute path."""
    out = []
    for n in TOOL_NAMES:
        out += [n, str(bin_dir() / n)]
    return out


# ---- Claude Code --------------------------------------------------------------------


def claude_run(task, arm, ws, prompt, model, timeout, log):
    cmd = ["claude", "-p", prompt, "--output-format", "stream-json", "--verbose", "--setting-sources", "project",
           "--strict-mcp-config"]
    if model:
        cmd += ["--model", model]
    env = dict(os.environ)
    if arm == "bash":
        cmd += ["--allowedTools", "Bash", "--disallowedTools", *CLAUDE_BUILTINS]
    elif arm == "mcp":
        cfg = ws.parent / "mcp.json"
        server = mcp_server_command(ws)
        cfg.write_text(json.dumps({"mcpServers": {"cancho-tools": {"command": server[0], "args": server[1:]}}}))
        cmd += ["--mcp-config", str(cfg), "--allowedTools", "mcp__cancho-tools__*", "--disallowedTools", "Bash", *CLAUDE_BUILTINS]
    else:
        skills_into(ws, ".claude/skills")
        env["PATH"] = str(bin_dir()) + os.pathsep + env["PATH"]
        cmd += ["--allowedTools", *["Bash(%s:*)" % p for p in tool_patterns()], "Skill", "--disallowedTools", *CLAUDE_BUILTINS]
    code, out, err = spawn(cmd, ws, env, timeout, log)
    return claude_parse(out), code, err


def claude_parse(out):
    r = {"answer": "", "turns": 0, "tokens_in": 0, "tokens_out": 0, "first_input": None, "cost": None, "calls": {}, "tool_errors": 0}
    for line in out.splitlines():
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if d.get("type") == "assistant":
            u = d["message"].get("usage") or {}
            if r["first_input"] is None:
                r["first_input"] = sum(u.get(k, 0) for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
            for c in d["message"]["content"]:
                if c.get("type") == "tool_use":
                    r["calls"][c["name"]] = r["calls"].get(c["name"], 0) + 1
        elif d.get("type") == "user":
            for c in d["message"]["content"] if isinstance(d["message"]["content"], list) else []:
                if isinstance(c, dict) and c.get("type") == "tool_result" and c.get("is_error"):
                    r["tool_errors"] += 1
        elif d.get("type") == "result":
            u = d.get("usage") or {}
            r.update(answer=d.get("result", "") or "", turns=d.get("num_turns", 0), cost=d.get("total_cost_usd"),
                     tokens_in=sum(u.get(k, 0) for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")),
                     tokens_out=u.get("output_tokens", 0))
    return r


# ---- opencode ---------------------------------------------------------------------------


def opencode_config(arm, ws, model):
    provider, _, name = model.partition("/")
    cfg = {"$schema": "https://opencode.ai/config.json",
           "provider": {provider: {"npm": "@ai-sdk/openai-compatible", "name": provider,
                                   "options": {"baseURL": os.environ.get("OLLAMA_URL", "http://localhost:11434/v1")},
                                   "models": {name: {"name": name}}}},
           "tools": {t: False for t in ["bash", "read", "write", "edit", "patch", "glob", "grep", "list", "webfetch",
                                        "todowrite", "todoread", "task", "skill"]},
           "permission": {"edit": "deny", "webfetch": "deny"}}
    if arm == "bash":
        cfg["tools"]["bash"] = True
        cfg["permission"]["bash"] = "allow"
    elif arm == "mcp":
        cfg["permission"]["bash"] = "deny"
        cfg["mcp"] = {"cancho-tools": {"type": "local", "command": mcp_server_command(ws), "enabled": True}}
    else:
        cfg["tools"]["bash"] = True
        cfg["tools"]["skill"] = True
        cfg["permission"]["bash"] = {"*": "deny", **{"%s *" % p: "allow" for p in tool_patterns()}}
    return cfg


def opencode_run(task, arm, ws, prompt, model, timeout, log):
    (ws / "opencode.json").write_text(json.dumps(opencode_config(arm, ws, model), indent=1))
    xdg = ws.parent / "xdg"
    for d in ("config", "data", "cache"):
        (xdg / d).mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "XDG_CONFIG_HOME": str(xdg / "config"), "XDG_DATA_HOME": str(xdg / "data"), "XDG_CACHE_HOME": str(xdg / "cache")}
    if arm == "skills":
        skills_into(ws, ".opencode/skills")
        env["PATH"] = str(bin_dir()) + os.pathsep + env["PATH"]
    code, out, err = spawn(["opencode", "run", "--pure", "--dir", str(ws), "--model", model, "--format", "json", prompt], ws, env, timeout, log)
    return opencode_parse(out), code, err


def opencode_parse(out):
    r = {"answer": "", "turns": 0, "tokens_in": 0, "tokens_out": 0, "first_input": None, "cost": 0.0, "calls": {}, "tool_errors": 0}
    texts = []
    for line in out.splitlines():
        try:
            d = json.loads(line)
        except ValueError:
            continue
        p = d.get("part") or {}
        if d.get("type") == "step_start":
            texts = []  # the answer is the text of the final step: narration before a call is not it
        elif d.get("type") == "text":
            texts.append(p.get("text", ""))
        elif d.get("type") == "tool_use":
            r["calls"][p.get("tool", "?")] = r["calls"].get(p.get("tool", "?"), 0) + 1
            if (p.get("state") or {}).get("status") == "error":
                r["tool_errors"] += 1
        elif d.get("type") == "step_finish":
            t = p.get("tokens") or {}
            r["turns"] += 1
            r["tokens_in"] += t.get("input", 0) + (t.get("cache") or {}).get("read", 0)
            r["tokens_out"] += t.get("output", 0)
            if r["first_input"] is None:
                r["first_input"] = t.get("input", 0) + (t.get("cache") or {}).get("read", 0)
            r["cost"] += p.get("cost") or 0
    r["answer"] = "\n".join(texts).strip()
    return r


def spawn(cmd, cwd, env, timeout, log):
    """Run `cmd`; on a timeout, kill its whole process group. Answers
    `(exit code or 'timeout', stdout, stderr)`; both streams go to `log`."""
    # `cwd=` does not change `$PWD`, and an agent that trusts `$PWD` works in the
    # directory the benchmark was started from -- the repository, not the
    # workspace. Found when the first opencode run's session directory was the repo.
    env = {**env, "PWD": str(cwd)}
    p = subprocess.Popen(cmd, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         text=True, start_new_session=True)
    try:
        out, err = p.communicate(timeout=timeout)
        code = p.returncode
    except subprocess.TimeoutExpired:
        os.killpg(p.pid, signal.SIGKILL)
        out, err = p.communicate()
        code = "timeout"
    pathlib.Path(log).write_text(out or "")
    pathlib.Path(str(log) + ".err").write_text(err or "")
    return code, out or "", err or ""


AGENTS = {"claude": claude_run, "opencode": opencode_run}


# ---- running ------------------------------------------------------------------------------


def key(r):
    return (r["agent"], r["model"], r["arm"], r["task"], r["rep"])


def run_one(agent, model, arm, task, rep, out_dir, timeout):
    run_dir, ws, facts = new_workspace(task)
    log = out_dir / "transcripts" / ("%s-%s-%s-%s-%d.jsonl" % (agent, model.replace("/", "_").replace(":", "_"), arm, task.id, rep))
    log.parent.mkdir(parents=True, exist_ok=True)
    prompt = PREFACE + tasks.fill(task.prompt, facts)
    started = time.time()
    try:
        parsed, code, err = AGENTS[agent](task, arm, ws, prompt, model, timeout, log)
        ok, why = task.check(ws, parsed["answer"], facts)
        parsed["litter"] = tasks.litter(ws, facts)
    finally:
        seconds = time.time() - started
    harness = ""
    if code == "timeout":
        harness = "timed out after %ds" % timeout
    elif code != 0:
        harness = "agent exited %s: %s" % (code, err.strip()[-200:])
    elif parsed["turns"] == 0:
        harness = "no turns recorded"
    shutil.rmtree(run_dir, ignore_errors=True)
    return {"agent": agent, "model": model, "arm": arm, "task": task.id, "category": task.category, "rep": rep,
            "ok": bool(ok), "why": why, "harness": harness, "seconds": round(seconds, 1), "transcript": str(log), **parsed}


def cmd_run(a):
    out_dir = pathlib.Path(a.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    results = out_dir / "results.jsonl"
    done = {key(json.loads(l)) for l in results.read_text().splitlines()} if results.exists() else set()
    chosen = [t for t in tasks.TASKS if (a.pilot and t.pilot) or (not a.pilot and (not a.tasks or t.id in a.tasks.split(",")))]
    if a.pilot and a.tasks:
        chosen = [t for t in chosen if t.id in a.tasks.split(",")]
    arms = a.arms.split(",")
    plan = [(arm, t, rep) for rep in range(a.reps) for t in chosen for arm in arms
            if (a.agent, a.model, arm, t.id, rep) not in done]
    print("%d runs to do (%d recorded)" % (len(plan), len(done)), flush=True)
    for n, (arm, t, rep) in enumerate(plan, 1):
        r = run_one(a.agent, a.model, arm, t, rep, out_dir, a.timeout)
        with results.open("a") as f:
            f.write(json.dumps(r) + "\n")
        print("[%d/%d] %-8s %-18s rep %d: %s  %s turns, %s s%s" % (n, len(plan), arm, t.id, rep, "pass" if r["ok"] else "FAIL",
                                                                   r["turns"], r["seconds"],
                                                                   "" if r["ok"] else "  (%s)" % (r["harness"] or r["why"])[:110]), flush=True)


# ---- reporting ------------------------------------------------------------------------------


def wilson(k, n, z=1.96):
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    m = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - m) / d, (c + m) / d)


def cmd_report(a):
    rows = [json.loads(l) for l in pathlib.Path(a.results).read_text().splitlines() if l.strip()]
    for agent_model in sorted({(r["agent"], r["model"]) for r in rows}):
        mine = [r for r in rows if (r["agent"], r["model"]) == agent_model]
        print("\n## %s, %s: %d runs, %d tasks" % (*agent_model, len(mine), len({r["task"] for r in mine})))
        print("\n| category | arm | passed | 95% interval | turns | tokens in | first call | seconds | litter |")
        print("|---|---|---|---|---|---|---|---|---|")
        for cat in ("read", "edit", "safety", "scope"):
            for arm in ARMS:
                rs = [r for r in mine if r["category"] == cat and r["arm"] == arm and not r["harness"]]
                if not rs:
                    continue
                k = sum(r["ok"] for r in rs)
                lo, hi = wilson(k, len(rs))
                passed = [r for r in rs if r["ok"]] or rs
                med = lambda f: "%d" % statistics.median([f(r) for r in passed if f(r) is not None] or [0])
                print("| %s | %s | %d/%d | %.0f%%–%.0f%% | %s | %s | %s | %s | %d |" % (
                    cat, arm, k, len(rs), 100 * lo, 100 * hi, med(lambda r: r["turns"]), med(lambda r: r["tokens_in"]),
                    med(lambda r: r["first_input"]), med(lambda r: r["seconds"]), sum(r.get("litter", 0) for r in rs)))
        harness = [r for r in mine if r["harness"]]
        if harness:
            print("\nHarness problems, not scored (§7): %d" % len(harness))
            for r in harness:
                print("  %s %s rep %d: %s" % (r["arm"], r["task"], r["rep"], r["harness"][:140]))
        print("\nFailures:")
        for r in mine:
            if not r["ok"] and not r["harness"]:
                print("  %-8s %-18s rep %d: %s" % (r["arm"], r["task"], r["rep"], r["why"][:150]))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("verify")
    r = sub.add_parser("run")
    r.add_argument("--agent", choices=sorted(AGENTS), required=True)
    r.add_argument("--model", default="")
    r.add_argument("--arms", default=",".join(ARMS))
    r.add_argument("--tasks", default="")
    r.add_argument("--pilot", action="store_true")
    r.add_argument("--reps", type=int, default=1)
    r.add_argument("--timeout", type=int, default=600)
    r.add_argument("--out", default="bench-out")
    p = sub.add_parser("report")
    p.add_argument("results")
    a = ap.parse_args()
    if a.cmd == "verify":
        problems = verify(bin_dir())
        print("\n".join(problems) or "every task: the reference solutions pass, doing nothing fails, the unsafe solution fails")
        sys.exit(1 if problems else 0)
    {"run": cmd_run, "report": cmd_report}[a.cmd](a)


if __name__ == "__main__":
    main()
