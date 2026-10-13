import pytest
from infrastructure.workers import oauth_worker


@pytest.mark.parametrize('outcome', ['database_error', 'job_error', 'healthy'])
def test_scheduler_heartbeat_requires_successful_work(monkeypatch, tmp_path, outcome):
    heartbeat = tmp_path / 'heartbeat'
    monkeypatch.setattr(oauth_worker, 'Path', lambda _: heartbeat)
    def cycle():
        if outcome == 'database_error':
            raise RuntimeError('database unavailable')
        return outcome == 'healthy'
    monkeypatch.setattr(oauth_worker, 'cycle', cycle)
    oauth_worker.tick()
    assert heartbeat.exists() is (outcome == 'healthy')
