import sqlite3,tempfile,threading,unittest
from pathlib import Path
from app import Library

class SessionConnectionsTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.lib=Library(Path(self.tmp.name));self.lib.start_session()
    def tearDown(self):self.lib.stop_session();self.tmp.cleanup()
    def test_reuse_and_cursor_lifetime(self):
        seen=set()
        for i in range(1000):
            with self.lib.connect() as db:
                seen.add(id(db));cursor=db.execute('SELECT 1');self.assertEqual(cursor.fetchone()[0],1)
            with self.assertRaises(sqlite3.ProgrammingError):cursor.fetchone()
        self.assertEqual(len(seen),1)
    def test_nested_leases_and_rollback(self):
        with self.lib.connect() as outer:
            with self.lib.connect() as inner:self.assertIsNot(outer,inner)
        with self.assertRaises(ValueError):
            with self.lib.connect() as db:
                db.execute("INSERT INTO meta VALUES ('rollback-test','no')");raise ValueError()
        self.assertEqual(self.lib.meta('rollback-test'),'')
    def test_concurrent_exclusive_leases(self):
        barrier=threading.Barrier(4);connections=[];errors=[]
        def run():
            try:
                with self.lib.connect() as db:
                    connections.append(db);barrier.wait(timeout=10)
                    self.assertEqual(db.execute('SELECT 7').fetchone()[0],7)
            except Exception as exc:errors.append(exc)
        threads=[threading.Thread(target=run) for _ in range(4)]
        for thread in threads:thread.start()
        for thread in threads:thread.join(15)
        self.assertFalse(errors);self.assertEqual(len({id(db) for db in connections}),4)
    def test_stop_closes_free_and_active_after_return(self):
        with self.lib.connect() as db:
            self.lib.stop_session();self.assertEqual(db.execute('SELECT 1').fetchone()[0],1)
        with self.assertRaises(sqlite3.ProgrammingError):db.execute('SELECT 1')
        self.assertFalse(self.lib.connection_pool)
    def test_non_session_closes_immediately(self):
        self.lib.stop_session()
        with self.lib.connect() as db:db.execute('SELECT 1')
        with self.assertRaises(sqlite3.ProgrammingError):db.execute('SELECT 1')
