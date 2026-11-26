"""Controlled engine processes for runner lifecycle tests; no container daemon or inference."""

import json
import importlib.util
import os
from pathlib import Path
import subprocess
import sys

import pytest


HELPER = Path(__file__).resolve().parents[1] / "run.py"


@pytest.fixture
def engine_factory(tmp_path):
    """Create an engine that tracks resources and emits private reviewer events."""
    def create(*, exit_code=0, hang=False):
        engine = tmp_path / "engine"
        state = tmp_path / "engine-state.json"
        state.write_text(json.dumps({"containers": {}, "network": False, "calls": []}))
        engine.write_text(
            f"#!{sys.executable}\n"
            f"STATE = {str(state)!r}\nEXIT_CODE = {exit_code!r}\nHANG = {hang!r}\n" + '''
import json, pathlib, signal, sys, time
path = pathlib.Path(STATE)
state = json.loads(path.read_text())
args = sys.argv[1:]
state['calls'].append(args)
def save():
    path.write_text(json.dumps(state))
if args[:2] == ['network', 'create']:
    state['network'] = True
elif args[:2] == ['network', 'ls']:
    if state['network']:
        print('network-id')
elif args[:2] == ['network', 'rm']:
    state['network'] = False
elif args[0] == 'run':
    name = args[args.index('--name') + 1]
    state['containers'][name] = True
    save()
    if '--detach' not in args:
        print(json.dumps({'type': 'reasoning', 'text': 'private model marker'}), flush=True)
        print(json.dumps({'type': 'private event marker'}), flush=True)
        print('private stderr credential marker', file=sys.stderr, flush=True)
        if HANG:
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
            while True:
                time.sleep(60)
        del state['containers'][name]
        save()
        sys.exit(EXIT_CODE)
elif args[0] == 'ps':
    print('\\n'.join(state['containers']))
elif args[0] == 'rm':
    for name in args[2:]:
        state['containers'].pop(name, None)
elif args[0] == 'inspect':
    print('true')
elif args[0] != 'exec':
    sys.exit(2)
save()
''', encoding="utf-8")
        engine.chmod(0o700)
        return engine, state
    return create


@pytest.fixture
def trusted_runner(tmp_path):
    root = tmp_path / "default-branch"
    root.mkdir()
    instructions = root / "AGENTS.md"
    instructions.write_text("trusted repository instructions\n")
    return HELPER, instructions


@pytest.fixture
def checkout(tmp_path):
    checkout = tmp_path / "pr-source"
    checkout.mkdir()
    (checkout / "app.py").write_text("important source\n")
    environment = dict(os.environ, GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
    for args in [
        ["init", "-b", "main"], ["add", "app.py"],
        ["-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
         "commit", "-m", "temporary fixture"],
    ]:
        subprocess.run(["git", "-C", str(checkout), *args], env=environment,
                       check=True, capture_output=True)
    return checkout


@pytest.fixture
def runner(monkeypatch):
    monkeypatch.syspath_prepend(str(HELPER.parent))
    spec = importlib.util.spec_from_file_location("review_runner", HELPER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
