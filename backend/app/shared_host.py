"""Password-protected common workspace with bounded AI entry points."""
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import hmac
import json
import os
import re
import sqlite3
import threading
from flask import g, jsonify, request, send_from_directory
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash

AI_PATHS = {
    '/api/graph/ontology/generate', '/api/graph/build',
    '/api/simulation/prepare', '/api/simulation/start',
    '/api/report/generate', '/api/report/chat',
    '/api/simulation/interview', '/api/simulation/interview/batch',
}

class DailyBudget:
    def __init__(self, path):
        self.path = str(path)
        with sqlite3.connect(self.path) as db:
            db.execute('CREATE TABLE IF NOT EXISTS usage (day TEXT, kind TEXT, count INTEGER NOT NULL, PRIMARY KEY(day,kind))')

    def admit(self, new_project, project_limit, request_limit):
        day = datetime.now(ZoneInfo('Europe/Budapest')).date().isoformat()
        with sqlite3.connect(self.path, timeout=10) as db:
            db.execute('BEGIN IMMEDIATE')
            counts = dict(db.execute('SELECT kind,count FROM usage WHERE day=?', (day,)))
            if counts.get('ai', 0) >= request_limit:
                return False
            if new_project and counts.get('projects', 0) >= project_limit:
                return False
            for kind in (['ai', 'projects'] if new_project else ['ai']):
                db.execute('INSERT INTO usage VALUES (?,?,1) ON CONFLICT(day,kind) DO UPDATE SET count=count+1', (day,kind))
        return True

def create_shared_app(config_class=None, *, frontend_dir=None, password_hash=None):
    from . import create_app
    from .config import Config
    app = create_app(config_class or Config)
    app.before_request_funcs[None] = [hook for hook in app.before_request_funcs.get(None, []) if hook.__name__ != 'log_request']
    app.config['MAX_CONTENT_LENGTH'] = 2 * 1024 * 1024
    import logging
    logging.getLogger('mirofish').setLevel(logging.INFO)
    expected_user = os.environ.get('MIROFISH_ACCESS_USERNAME', 'mirofish')
    expected_hash = password_hash or os.environ.get('MIROFISH_ACCESS_PASSWORD_HASH', '')
    if not expected_hash:
        raise RuntimeError('MIROFISH_ACCESS_PASSWORD_HASH is required; shared access stays closed')
    root = Path(app.config['UPLOAD_FOLDER']).resolve()
    root.mkdir(parents=True, exist_ok=True)
    budget = DailyBudget(root / 'shared-usage.sqlite3')
    front = Path(frontend_dir or Path(__file__).resolve().parents[2] / 'frontend/dist')
    admission_lock = threading.Lock()

    def own_graph(graph_id):
        for project in (root / 'projects').glob('proj_*/project.json'):
            try:
                if json.loads(project.read_text()).get('graph_id') == graph_id:
                    return True
            except (OSError, ValueError):
                continue
        return False

    @app.before_request
    def shared_guard():
        if request.path == '/health' and request.method in {'GET', 'HEAD'}:
            return None
        auth = request.authorization
        if not (auth and auth.type == 'basic'
                and hmac.compare_digest(auth.username or '', expected_user)
                and check_password_hash(expected_hash, auth.password or '')):
            response = jsonify(success=False, error='A közös MiroFish-munkatérhez belépés szükséges.')
            response.status_code = 401
            response.headers['WWW-Authenticate'] = 'Basic realm="MiroFish", charset="UTF-8"'
            return response
        if request.method not in {'GET', 'HEAD', 'OPTIONS'}:
            if request.headers.get('Origin') != request.host_url.rstrip('/'):
                return jsonify(success=False, error='Csak a MiroFish saját oldaláról indítható művelet.'), 403
        if request.path.startswith('/api/graph/delete/'):
            return jsonify(success=False, error='Közvetlen felhős hálótörlés a megosztott változatban nem engedélyezett.'), 403
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            return jsonify(success=False, error='Érvénytelen kérés.'), 400
        graph_ids = re.findall(r'mirofish_[a-zA-Z0-9]+', request.path)
        if isinstance(data.get('graph_id'), str):
            graph_ids.append(data['graph_id'])
        if any(not own_graph(gid) for gid in graph_ids):
            return jsonify(success=False, error='Ez a kapcsolati háló nem ehhez a közös munkatérhez tartozik.'), 403
        if request.method == 'POST' and request.path in AI_PATHS:
            admission_lock.acquire()
            g.shared_admission_locked = True
        if request.path == '/api/simulation/start' and request.method == 'POST':
            try:
                rounds = int(data.get('max_rounds', os.environ.get('MIROFISH_MAX_ROUNDS', '20')))
            except (TypeError, ValueError):
                return jsonify(success=False, error='Érvénytelen körszám.'), 400
            maximum = int(os.environ.get('MIROFISH_MAX_ROUNDS', '20'))
            if rounds <= 0 or rounds > maximum:
                return jsonify(success=False, error=f'A közös változatban legfeljebb {maximum} kör indítható.'), 400
            request.get_json()['max_rounds'] = rounds
            from .services.simulation_runner import SimulationRunner
            if any(process.poll() is None for process in SimulationRunner._processes.values()):
                return jsonify(success=False, error='Már fut egy vizsgálat. Várd meg a befejezését.'), 409
        if request.method == 'POST' and request.path in AI_PATHS:
            if not budget.admit(
                request.path == '/api/graph/ontology/generate',
                int(os.environ.get('MIROFISH_NEW_PROJECTS_PER_DAY', '3')),
                int(os.environ.get('MIROFISH_AI_REQUESTS_PER_DAY', '24')),
            ):
                return jsonify(success=False, error='A közös munkatér napi futtatási kerete elfogyott. Holnap újra használható.'), 429
        return None

    @app.teardown_request
    def release_admission(_error):
        if g.pop('shared_admission_locked', False):
            admission_lock.release()

    @app.route('/', defaults={'path': ''})
    @app.route('/<path:path>')
    def shared_frontend(path):
        if path == 'robots.txt':
            return 'User-agent: *\nDisallow: /\n', 200, {'Content-Type': 'text/plain'}
        if path == 'source.zip':
            return send_from_directory(Path(__file__).resolve().parents[2] / 'cloud', 'source.zip', as_attachment=True)
        if path.startswith('api/'):
            return jsonify(success=False, error='Ismeretlen végpont.'), 404
        if path and (front / path).is_file():
            return send_from_directory(front, path)
        return send_from_directory(front, 'index.html')

    original_wsgi = app.wsgi_app
    def hardened_wsgi(environ, start_response):
        def safe_start(status, headers, exc_info=None):
            headers = [(key,value) for key,value in headers if not key.lower().startswith('access-control-allow-')]
            headers += [('X-Content-Type-Options', 'nosniff'), ('Referrer-Policy', 'no-referrer'),
                        ('X-Frame-Options', 'DENY'), ('X-Robots-Tag', 'noindex, nofollow'), ('Cache-Control', 'no-store')]
            return start_response(status, headers, exc_info)
        return original_wsgi(environ, safe_start)
    app.wsgi_app = ProxyFix(hardened_wsgi, x_for=1, x_proto=1)
    return app
