import os
import struct
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import machine
from machine import GB, MB, Profile, parse_core_records


def core(efficiency, smt):
    # PROCESSOR_RELATIONSHIP: Flags, EfficiencyClass, Reserved[20], GroupCount (WORD @22), GROUP_AFFINITY (16 bytes @24)
    body = bytes([1 if smt else 0, efficiency]) + bytes(20) + struct.pack('<H', 1) + bytes(16)
    return struct.pack('<II', 0, 8 + len(body)) + body


def cache_record():
    return struct.pack('<II', 2, 48) + bytes(40)   # RelationCache, skipped


class MachineTest(unittest.TestCase):
    def test_hybrid_core_records(self):
        raw = b''.join([core(1, True)] * 8 + [cache_record()] + [core(0, False)] * 16)
        self.assertEqual(parse_core_records(raw), (32, 24, 8, 16))

    def test_uniform_core_records(self):
        self.assertEqual(parse_core_records(b''.join([core(0, True)] * 8)), (16, 8, 8, 0))
        with self.assertRaises(ValueError):
            parse_core_records(cache_record())

    def test_decoders_scale_with_cores_and_memory(self):
        self.assertEqual(Profile(32, 24, 8, 16, 64 * GB).decoders(), 12)   # i9-14900HX
        self.assertEqual(Profile(16, 8, 8, 0, 16 * GB).decoders(), 6)
        self.assertEqual(Profile(4, 4, 4, 0, 16 * GB).decoders(), 3)
        self.assertEqual(Profile(8, 8, 8, 0, 2 * GB).decoders(), 2)        # memory bound
        with patch.dict(os.environ, {'PHOTOSIEVE_DECODERS': '5'}):
            self.assertEqual(Profile(32, 24, 8, 16, 64 * GB).decoders(), 5)

    def test_interface_threads_stay_few(self):
        self.assertEqual(Profile(32, 24, 8, 16, 64 * GB).interface_threads(), 4)
        self.assertEqual(Profile(4, 4, 4, 0, 16 * GB).interface_threads(), 2)

    def test_buffer_holds_100_to_200_previews_within_memory(self):
        self.assertEqual(Profile(32, 24, 8, 16, 64 * GB).buffer(6 * MB), 200)
        self.assertEqual(Profile(16, 8, 8, 0, 16 * GB).buffer(6 * MB), 109)
        self.assertEqual(Profile(8, 4, 4, 0, 8 * GB).buffer(6 * MB), 54)
        self.assertEqual(Profile(4, 2, 2, 0, 1 * GB).buffer(6 * MB), 24)

    def test_describe(self):
        self.assertIn('8P+16E', Profile(32, 24, 8, 16, 64 * GB).describe())
        self.assertIn('64 GB', Profile(32, 24, 8, 16, 64 * GB).describe())

    def test_cache_default_follows_free_space(self):
        for free, expected in ((1000 * GB, 20 * 1024), (200 * GB, 10 * 1024), (10 * GB, 2048)):
            with patch('machine.shutil.disk_usage', return_value=SimpleNamespace(total=0, used=0, free=free)):
                self.assertEqual(machine.cache_default_mb('.'), expected)
        with patch('machine.shutil.disk_usage', side_effect=OSError):
            self.assertEqual(machine.cache_default_mb('.'), 2048)

    def test_platform_calls_never_raise(self):
        self.assertIn(machine.seek_penalty('/nonexistent/folder'), (None, True, False))
        if os.name != 'nt':
            self.assertFalse(machine.decoder_qos())
            self.assertFalse(machine.interface_qos())
            self.assertFalse(machine.fine_timer())


if __name__ == '__main__':
    unittest.main()
