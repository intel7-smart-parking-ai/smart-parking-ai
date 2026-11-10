"""Local observations. Only committed frames are advertised as collected."""
import json
import logging
import queue
import sqlite3
import threading
from uuid import uuid4


class FrameCollection:
    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        self.committed = threading.Condition(self.lock)
        self.jobs = queue.Queue(maxsize=32)
        self.session = None
        self.latest = {}
        self.saved = {}
        self.failed = {}
        self.error = {}
        self.writer = threading.Thread(target=self._write, name='v53-local-collection', daemon=True)
        self.writer.start()

    def begin(self):
        with self.lock:
            self.session = uuid4().hex
            self.latest.clear()
            self.saved.clear()
            self.failed.clear()
            self.error.clear()
            self.committed.notify_all()

    def wait_for_frame(self, seen, timeout):
        with self.committed:
            session = self.session
            self.committed.wait_for(lambda: self.session != session or any(
                key != seen.get(camera) for camera, (key, _) in self.latest.items()), timeout)

    def offer(self, camera, result, timely):
        with self.lock:
            try:
                self.jobs.put_nowait((self.session, camera, result, timely))
            except queue.Full:
                self._failure(camera, '디스크 기록 대기열 가득 참')

    def _failure(self, camera, message):
        self.failed[camera] = self.failed.get(camera, 0) + 1
        self.error[camera] = message

    def _connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=1)
        try:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('''CREATE TABLE IF NOT EXISTS observations (
                session TEXT NOT NULL, channel TEXT NOT NULL, epoch INTEGER NOT NULL,
                sequence INTEGER NOT NULL, observed_at TEXT NOT NULL, lot TEXT,
                timely INTEGER NOT NULL, payload TEXT NOT NULL,
                PRIMARY KEY(session, channel, epoch, sequence))''')
        except Exception:
            db.close()
            raise
        return db

    def _write(self):
        db = None
        try:
            while True:
                job = self.jobs.get()
                if job is None:
                    return
                session, camera, result, timely = job
                try:
                    if db is None:
                        db = self._connect()
                    data = {k:v for k,v in result.items() if k != 'annotated_image'}
                    epoch, seq = data['stream_epoch'], data['frame_sequence']
                    payload = json.dumps(data, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
                    with db:
                        inserted = db.execute('INSERT OR IGNORE INTO observations VALUES (?,?,?,?,?,?,?,?)',
                            (session, camera, epoch, seq, data['observed_at'], data.get('lot_id'),
                             int(timely), payload)).rowcount
                    with self.lock:
                        if session == self.session and inserted:
                            self.saved[camera] = self.saved.get(camera, 0) + 1
                            self.latest[camera] = (f'{session}.{epoch}.{seq}', result)
                            self.error.pop(camera, None)
                            self.committed.notify_all()
                except (OSError, sqlite3.Error, ValueError, TypeError) as error:
                    logging.getLogger(__name__).exception('Cannot save camera %s observation', camera)
                    if db:
                        db.close()
                        db = None
                    with self.lock:
                        if session == self.session:
                            self._failure(camera, '로컬 저장 실패 · ' + type(error).__name__)
        finally:
            if db:
                db.close()

    def snapshot(self, seen, include_frames):
        with self.lock:
            channels = {}
            for camera in ('1', '2'):
                key, result = self.latest.get(camera, (None, None))
                channels[camera] = dict(key=key, saved=self.saved.get(camera, 0),
                    failed=self.failed.get(camera, 0), error=self.error.get(camera))
                if include_frames and key and seen.get(camera) != key:
                    channels[camera]['result'] = result
            return dict(session=self.session, channels=channels)

    def close(self):
        self.jobs.put(None)
        self.writer.join()
