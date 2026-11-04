"""Analysis-owned, exact-input checkpoints. No images or credentials on disk."""
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
from threading import Lock
from time import perf_counter
from types import SimpleNamespace
from uuid import uuid4


def digest(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f'{uuid4().hex[:16]}.tmp')
    try:
        with temporary.open('w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class AnalysisReuse:
    def __init__(self, directory: Path, identity: dict):
        self.directory = directory / 'resume' / digest(identity)[:32]
        self.lock = Lock()
        self.request_locks = {}
        self.stats = {'api_calls': 0, 'request_reused': 0, 'decision_reused': 0, 'stage_reused': [],
                      'input_tokens': 0, 'output_tokens': 0,
                      'reused_input_tokens': 0, 'reused_output_tokens': 0,
                      'api_seconds': 0.0, 'api_errors': 0}

    @classmethod
    def for_job(cls, directory, source, models, calibration, settings):
        def stamp(path):
            if not path or not path.exists():
                return None
            files = sorted(path.rglob('*')) if path.is_dir() else [path]
            return [(str(p.resolve()), p.stat().st_size, p.stat().st_mtime_ns)
                    for p in files if p.is_file()]
        # Any analysis code change invalidates the namespace, including prompts
        # and geometry rules. Authentication secrets never enter this identity.
        code = sha256()
        for path in sorted(Path(__file__).parents[1].rglob('*.py')):
            code.update(str(path.relative_to(Path(__file__).parents[1])).encode())
            code.update(path.read_bytes())
        options = {key: getattr(settings, key) for key in (
            'candidate_runtime', 'openai_vision_model', 'openai_vision_fallback_model',
            'openai_timeout_seconds', 'openai_shot_review_enabled',
            'openai_max_candidate_reviews', 'openai_max_shot_gate_reviews')}
        identity = {'version': 1, 'source': stamp(source), 'api_configured': bool(settings.openai_api_key),
            'models': [stamp(p) for p in models], 'calibration': calibration,
            'options': options, 'code': code.hexdigest()}
        pointer = directory / 'resume' / 'current.json'
        try:
            previous = json.loads(pointer.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            previous = {}
        run = previous.get('run', '') if previous.get('identity') == digest(identity) else ''
        reuse = cls(directory, {**identity, 'run': run})
        if not run or (reuse.directory / 'completed.json').exists():
            run = uuid4().hex
            reuse = cls(directory, {**identity, 'run': run})
            atomic_json(pointer, {'identity': digest(identity), 'run': run})
        return reuse

    def load_stage(self, name):
        path = self.directory / f'{name}.json'
        try:
            value = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return None
        self.stats['stage_reused'].append(name)
        return value

    def save_stage(self, name, value):
        atomic_json(self.directory / f'{name}.json', value)

    def client(self, client):
        return SimpleNamespace(responses=SimpleNamespace(parse=lambda **kw: self.parse(client, **kw)),
                               cached_review=self.cached_review)

    def cached_review(self, identity, compute):
        """Skip image preparation as well as the identical gate request."""
        key = digest(identity)[:40]
        with self.lock:
            request_lock = self.request_locks.setdefault('review:' + key, Lock())
        with request_lock:
            path = self.directory / 'reviews' / f'{key}.json'
            try:
                saved = json.loads(path.read_text(encoding='utf-8'))
                if saved.get('status') != 'complete':
                    saved = None
            except (OSError, ValueError, AttributeError):
                saved = None
            if saved is not None:
                with self.lock:
                    self.stats['decision_reused'] += 1
                    for field in ('input_tokens', 'output_tokens'):
                        self.stats[f'reused_{field}'] += int((saved.get('usage') or {}).get(field, 0))
                return {**saved, 'usage': {'input_tokens': 0, 'output_tokens': 0},
                        'seconds': 0, 'reused': True}
            value = compute()
            if value.get('status') == 'complete':
                atomic_json(path, value)
            return value

    def parse(self, client, **kwargs):
        schema = kwargs['text_format']
        request = {key: value for key, value in kwargs.items() if key != 'text_format'}
        key = digest({'request': request, 'schema': schema.model_json_schema()})[:40]
        with self.lock:
            request_lock = self.request_locks.setdefault(key, Lock())
        # Only identical requests wait for each other; unrelated shots retain
        # the existing parallelism. Parse afresh so downstream mutation is local.
        with request_lock:
            path = self.directory / 'requests' / f'{key}.json'
            try:
                saved = json.loads(path.read_text(encoding='utf-8'))
                parsed = schema.model_validate(saved['decision'])
            except (OSError, ValueError, KeyError, TypeError):
                saved = None
            if saved is not None:
                with self.lock:
                    self.stats['request_reused'] += 1
                    for field in ('input_tokens', 'output_tokens'):
                        self.stats[f'reused_{field}'] += saved['usage'].get(field, 0)
                return SimpleNamespace(output_parsed=parsed,
                    usage=SimpleNamespace(input_tokens=0, output_tokens=0, total_tokens=0))
            started = perf_counter()
            with self.lock:
                self.stats['api_calls'] += 1
            try:
                response = client.responses.parse(**deepcopy(kwargs))
            except Exception:
                with self.lock:
                    self.stats['api_errors'] += 1
                raise
            finally:
                with self.lock:
                    self.stats['api_seconds'] += perf_counter() - started
            usage = {field: int(getattr(getattr(response, 'usage', None), field, 0) or 0)
                     for field in ('input_tokens', 'output_tokens')}
            with self.lock:
                for field in usage:
                    self.stats[field] += usage[field]
            # Never persist exceptions, refusals, truncated or invalid responses.
            if response.output_parsed is not None:
                parsed = schema.model_validate(response.output_parsed.model_dump())
                atomic_json(path, {'decision': parsed.model_dump(mode='json'), 'usage': usage})
            return response
