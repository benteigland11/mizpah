import json

from mizpah import episodes


def turn(n, *calls, content=""):
    tool_calls, results = [], []
    for i, (name, text, status, code, out) in enumerate(calls):
        cid = f"c{n}_{i}"
        args = {"command": text} if name == "bash" else {"path": text}
        tool_calls.append({"id": cid, "function": {"name": name, "arguments": json.dumps(args)}})
        results.append({"call_id": cid, "name": name, "result": {"status": status, "exit_code": code, "stderr": out if code else "", "stdout": "" if code else out}})
    return {"event_type": "worker_turn", "created_at": "t", "payload": {"turn": n, "response": {"content": content, "tool_calls": tool_calls}, "tool_results": results}}


def write_task(tmp_path, events, rounds=None):
    task = tmp_path / "task"
    (task / "events").mkdir(parents=True)
    (task / "events" / "session.jsonl").write_text("\n".join(json.dumps(e) for e in events) + "\n")
    if rounds is not None:
        (task / "result.json").write_text(json.dumps({"verdict": "complete", "rounds": rounds}))
    return task


def test_family_reads_the_command_not_its_setup():
    call = lambda text: {"name": "bash", "text": text}
    assert episodes.family(call("cd /work && TERRA_MAP=m terra probe run x --json")) == "terra probe run"
    assert episodes.family(call("python3 scripts/render.py --out a")) == "python render.py"
    assert episodes.family(call("cartograph validate cg/data_x_python")) == "cartograph validate"
    assert episodes.family({"name": "terra_known_ladder", "text": "{}"}) == "terra_known_ladder"


def test_repeated_failures_then_a_pass_is_an_episode(tmp_path):
    task = write_task(tmp_path, [
        turn(1, ("bash", "terra probe run p", "ok", 1, "Traceback: ModuleNotFoundError: src")),
        turn(2, ("bash", "terra probe run p", "ok", 1, "Traceback: ModuleNotFoundError: src")),
        turn(3, ("edit", "measure.py", "ok", 0, "")),
        turn(4, ("bash", "terra probe run p", "ok", 0, "[ok] p")),
    ])
    found = episodes.mine(task, where="gym/task")
    assert len(found) == 1
    e = found[0]
    assert e["kind"] == "error" and e["family"] == "terra probe run" and e["cost"] == 4
    assert "ModuleNotFoundError" in e["symptom"] and "[ok] p" in e["trace"]
    assert e["where"] == "gym/task" and "turns 1-4" in e["source"]


def test_one_slip_or_a_looking_miss_is_not(tmp_path):
    task = write_task(tmp_path, [
        turn(1, ("bash", "python3 a.py", "ok", 1, "NameError")),
        turn(2, ("bash", "python3 a.py", "ok", 0, "done")),
        turn(3, ("read", "missing.md", "error", None, "no such file")),
        turn(4, ("read", "missing.md", "error", None, "no such file")),
        turn(5, ("read", "there.md", "ok", None, "text")),
    ])
    assert episodes.mine(task) == []


def test_red_then_green_is_an_episode_but_playbook_bookkeeping_is_not(tmp_path):
    events = [turn(n, ("bash", "echo", "ok", 0, "")) for n in range(1, 11)]
    task = write_task(tmp_path, events, rounds=[
        {"turns": 3, "gate": False, "problems": ["checklist .playbook/open/x.md has 1 unticked step(s)"]},
        {"turns": 4, "gate": True, "problems": []},
        {"turns": 6, "gate": False, "problems": ["known k reads true while count reads 0: a universal over nothing is vacuous"]},
        {"turns": 9, "gate": True, "problems": []},
    ])
    found = episodes.mine(task)
    assert [e["kind"] for e in found] == ["gate"]
    assert "vacuous" in found[0]["symptom"] and found[0]["cost"] == 3
