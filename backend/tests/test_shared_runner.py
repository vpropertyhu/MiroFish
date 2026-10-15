import json
from types import SimpleNamespace

import pytest
from app.services import simulation_runner as module
from app.services.simulation_runner import SimulationRunner

@pytest.mark.parametrize('platform,flag', [('reddit', '--reddit-only'), ('twitter', '--twitter-only'), ('parallel', None)])
def test_shared_runs_emit_progress_and_exit_without_browser(monkeypatch, tmp_path, platform, flag):
    sim_dir = tmp_path / 'sim_shared'
    sim_dir.mkdir()
    (sim_dir / 'simulation_config.json').write_text(json.dumps({'time_config': {'total_simulation_hours': 24, 'minutes_per_round': 60}}))
    monkeypatch.setattr(SimulationRunner, 'RUN_STATE_DIR', str(tmp_path))
    for name in ['_processes', '_run_states', '_monitor_threads', '_action_queues', '_stdout_files', '_stderr_files', '_graph_memory_enabled']:
        monkeypatch.setattr(SimulationRunner, name, {})
    monkeypatch.setattr(SimulationRunner, '_sync_simulation_status', classmethod(lambda cls, *args: None))
    monkeypatch.setenv('MIROFISH_SHARED_HOST', '1')
    calls = []
    def spawn(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(pid=1234, poll=lambda: None)
    monkeypatch.setattr(module.subprocess, 'Popen', spawn)
    monkeypatch.setattr(module.threading, 'Thread', lambda **kwargs: SimpleNamespace(start=lambda: None))
    try:
        SimulationRunner.start_simulation('sim_shared', platform=platform, max_rounds=2)
        command = calls[0]
        assert command[1].endswith('run_parallel_simulation.py')
        assert '--no-wait' in command
        assert command[-2:] == ['--max-rounds', '2']
        if flag:
            assert flag in command
        else:
            assert '--reddit-only' not in command and '--twitter-only' not in command
    finally:
        for handle in SimulationRunner._stdout_files.values():
            handle.close()
