"""Persistent FIFO file queue, shared by Flask/Gunicorn worker processes."""

from contextlib import closing, contextmanager
import errno
import json
import logging
import os
import sqlite3
import time


class TranscriptionQueue:
    def __init__(self, database, processor, on_error):
        self.database = database
        self.processor = processor
        self.on_error = on_error
        self.initialize_database(database)

    @staticmethod
    def initialize_database(database):
        """Mark interrupted processing jobs as errors during database startup."""
        os.makedirs(os.path.dirname(os.path.abspath(database)), exist_ok=True)
        with closing(sqlite3.connect(database, timeout=30)) as db, db:
            db.execute('''CREATE TABLE IF NOT EXISTS transcription_queue (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL UNIQUE,
                arguments TEXT NOT NULL
            )''')
        # A new Gunicorn worker can start while another is processing a file.
        # Only reset all processing records when no worker owns this lock.
        with TranscriptionQueue._database_processing_lock(database) as idle, \
                closing(sqlite3.connect(database, timeout=30)) as db, db:
            recovered = db.execute('''UPDATE transcriptions
                SET status = 'error', error_message = ?
                WHERE (status = 'processing' AND ?)
                   OR (status IN ('pending', 'processing')
                  AND NOT EXISTS (SELECT 1 FROM transcription_queue q
                                  WHERE q.job_id = transcriptions.id))''',
                ('Transcription was interrupted before completion. '
                 'Please upload this file again.', idle)).rowcount
            if recovered:
                logging.warning('[QUEUE] Marked %s abandoned transcription(s) as interrupted.', recovered)
        logging.info('[QUEUE] Database initialized at %s; recovered %s abandoned job(s).',
                     os.path.abspath(database), recovered)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.database, timeout=30)
        try:
            with db:
                yield db
        finally:
            db.close()

    def enqueue(self, *arguments, connection=None):
        if connection is not None:
            connection.execute('INSERT INTO transcription_queue (job_id, arguments) VALUES (?, ?)',
                               (arguments[0], json.dumps(arguments)))
            return
        with self.connection() as db:
            self.enqueue(*arguments, connection=db)

    def job_ids(self):
        with self.connection() as db:
            return [row[0] for row in db.execute('SELECT job_id FROM transcription_queue')]

    def processing_lock(self):
        return self._database_processing_lock(self.database)

    @staticmethod
    @contextmanager
    def _database_processing_lock(database):
        # Hold an OS lock for the whole file, without holding a SQLite transaction.
        # This serializes processing even with multiple Gunicorn workers.
        with open(database + '.processing.lock', 'a+b') as lock:
            if os.fstat(lock.fileno()).st_size == 0:
                lock.write(b'0')
                lock.flush()
            lock.seek(0)
            if os.name == 'nt':
                import msvcrt
                acquire = lambda: msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                release = lambda: msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                acquire = lambda: fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                release = lambda: fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
            try:
                acquire()
            except OSError as error:
                if error.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                    raise
                yield False
                return
            try:
                yield True
            finally:
                lock.seek(0)
                release()

    def run_next(self):
        with self.processing_lock() as acquired:
            if not acquired:
                return False
            with self.connection() as db:
                row = db.execute('''SELECT q.job_id, q.arguments, t.status
                    FROM transcription_queue q LEFT JOIN transcriptions t ON t.id = q.job_id
                    ORDER BY q.sequence LIMIT 1''').fetchone()
            if row is None:
                return False
            job_id, payload, status = row
            try:
                if status == 'pending':
                    self.processor(*json.loads(payload))
                elif status == 'processing':
                    # The lock is free, so a previous worker exited mid-job.
                    # Don't silently repeat a potentially billable API request.
                    self.on_error(job_id, 'Transcription was interrupted by an app restart. Please upload this file again.')
            except Exception:
                logging.exception('[QUEUE] Job %s failed', job_id)
                self.on_error(job_id, 'Queued transcription failed. See server logs for details.')
            with self.connection() as db:
                db.execute('DELETE FROM transcription_queue WHERE job_id = ?', (job_id,))
            return True

    def run_forever(self):
        while True:
            try:
                if self.run_next():
                    continue
            except Exception:
                logging.exception('[QUEUE] Unable to process the next file')
            time.sleep(1)
