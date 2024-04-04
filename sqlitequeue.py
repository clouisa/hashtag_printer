import os
import sys
import sqlite3
from pickle import loads, dumps
from time import sleep
from threading import get_ident
if sys.version_info > (3,):
    buffer = memoryview


class SqliteQueue(object):
    _create = (
        'CREATE TABLE IF NOT EXISTS queue '
        '('
        '  id INTEGER PRIMARY KEY AUTOINCREMENT,'
        '  item BLOB'
        ')'
    )
    _count = 'SELECT COUNT(*) FROM queue'
    _iterate = 'SELECT id, item FROM queue'
    _append = 'INSERT INTO queue (item) VALUES (?)'
    _write_lock = 'BEGIN IMMEDIATE'
    _popleft_get = (
        'SELECT id, item FROM queue '
        'ORDER BY id LIMIT 1'
    )
    _popleft_del = 'DELETE FROM queue WHERE id = ?'
    _peek = (
        'SELECT item FROM queue '
        'ORDER BY id LIMIT 1'
    )

    def __init__(self, path):
        self.path = os.path.abspath(path)
        self._connection_cache = {}
        with self._get_conn() as conn:
            conn.execute(self._create)

    def __len__(self):
        with self._get_conn() as conn:
            cursor = conn.cursor()
            l = cursor.execute(self._count).fetchone()[0]
        return l

    def __iter__(self):
        with self._get_conn() as conn:
            for id, obj_buffer in conn.execute(self._iterate):
                yield loads(str(obj_buffer))

    def _get_conn(self):
        id = get_ident()
        if id not in self._connection_cache:
            self._connection_cache[id] = sqlite3.Connection(self.path, timeout=60)
        return self._connection_cache[id]

    def append(self, obj):
        obj_buffer = buffer(dumps(obj, 2))
        with self._get_conn() as conn:
            conn.execute(self._append, (obj_buffer,))

    def popleft(self, sleep_wait=True):
        keep_pooling = True
        wait = 0.1
        max_wait = 2
        tries = 0
        with self._get_conn() as conn:
            id = None
            while keep_pooling:
                cursor = conn.cursor()
                cursor.execute(self._write_lock)
                cursor.execute(self._popleft_get)
                try:
                    row = cursor.fetchone()
                    if row:
                        id, obj_buffer = row
                    keep_pooling = False
                except StopIteration:
                    conn.commit()  # unlock the database
                    if not sleep_wait:
                        keep_pooling = False
                        continue
                    tries += 1
                    sleep(wait)
                    wait = min(max_wait, tries / 10 + wait)
            if id:
                conn.execute(self._popleft_del, (id,))
                return loads(obj_buffer)
        return None

    def peek(self):
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(self._peek)
            try:
                row = cursor.fetchone()
                if row:
                    obj_buffer = row[0]
                    return loads(obj_buffer)
                else:
                    return None
            except StopIteration:
                return None
