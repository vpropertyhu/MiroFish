import base64
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import json

import pytest
from werkzeug.security import generate_password_hash
from app.config import Config
from app.shared_host import DailyBudget, create_shared_app

@pytest.fixture
def host(tmp_path):
    class TestConfig(Config):
        TESTING = True
        UPLOAD_FOLDER = str(tmp_path / 'data')
    front = tmp_path / 'front'
    front.mkdir()
    (front / 'index.html').write_text('<title>MiroFish</title>')
    (front / 'asset.js').write_text('console.log(1)')
    app = create_shared_app(TestConfig, frontend_dir=front, password_hash=generate_password_hash('qa-test-only', method='pbkdf2:sha256:1000'))
    return app, tmp_path

def auth(origin='http://localhost'):
    return {'Authorization': 'Basic ' + base64.b64encode(b'mirofish:qa-test-only').decode(), 'Origin': origin}

def test_unauthenticated_frontend_and_api_are_closed_but_health_is_public(host):
    app,_ = host
    c = app.test_client()
    assert c.get('/').status_code == 401
    assert c.get('/api/graph/project/list').status_code == 401
    assert c.get('/health').status_code == 200

def test_wrong_password_and_missing_password_config_fail_closed(host, tmp_path, monkeypatch):
    app,_ = host
    assert app.test_client().get('/', headers={'Authorization':'Basic bWlyb2Zpc2g6d3Jvbmc='}).status_code == 401
    monkeypatch.delenv('MIROFISH_ACCESS_PASSWORD_HASH', raising=False)
    with pytest.raises(RuntimeError, match='PASSWORD_HASH'):
        create_shared_app(frontend_dir=tmp_path)

def test_authenticated_spa_and_static_asset(host):
    app,_ = host
    c = app.test_client()
    assert c.get('/simulation/sim_abc', headers=auth()).status_code == 200
    assert c.get('/asset.js', headers=auth()).data == b'console.log(1)'
    assert c.get('/api/unknown', headers=auth()).status_code == 404

def test_cross_origin_writes_blocked_and_cors_headers_removed(host):
    app,_ = host
    c=app.test_client()
    assert c.post('/api/report/chat', headers=auth('https://other.invalid'), json={}).status_code == 403
    response=c.get('/health', headers={'Origin':'https://other.invalid'})
    assert 'Access-Control-Allow-Origin' not in response.headers
    assert response.headers['X-Robots-Tag'] == 'noindex, nofollow'

def test_foreign_graph_cannot_be_read_or_deleted(host):
    app,_ = host
    c=app.test_client()
    assert c.get('/api/graph/data/mirofish_foreign', headers=auth()).status_code == 403
    assert c.delete('/api/graph/delete/mirofish_foreign', headers=auth()).status_code == 403

def test_round_limit_cannot_be_bypassed(host):
    app,_ = host
    c=app.test_client()
    for value in [0,-1,21,100000,'many']:
        assert c.post('/api/simulation/start', headers=auth(), json={'simulation_id':'sim_abc','max_rounds':value}).status_code == 400

def test_default_rounds_are_bounded_before_reaching_runner(host):
    app,_ = host
    rule=next(rule for rule in app.url_map.iter_rules() if rule.rule=='/api/simulation/start')
    from flask import request
    app.view_functions[rule.endpoint]=lambda: {'rounds':request.get_json()['max_rounds']}
    assert app.test_client().post('/api/simulation/start',headers=auth(),json={'simulation_id':'sim_abc'}).json['rounds']==20

def test_daily_budget_is_atomic_and_survives_reopen(tmp_path):
    p=tmp_path/'limits.sqlite3'
    DailyBudget(p)
    def attempt(_): return DailyBudget(p).admit(True,3,24)
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(attempt,range(20)))==3
    assert not DailyBudget(p).admit(True,3,24)

def test_forwarded_https_origin_works_behind_render(host):
    app,_=host
    rule=next(rule for rule in app.url_map.iter_rules() if rule.rule=='/api/report/chat')
    app.view_functions[rule.endpoint]=lambda: {'ok':True}
    headers=auth('https://localhost')
    headers['X-Forwarded-Proto']='https'
    assert app.test_client().post('/api/report/chat', headers=headers,json={}).status_code==200
