import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import embedded_rating as embedded
import native_rating as native


class EmbeddedRatingTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name).resolve()
        self.original=self.root/'照片.ARW'
        self.original.write_bytes(b'original RAW payload')
        self.environment=patch.dict(os.environ,PHOTOSIEVE_RATING_CANDIDATES=str(self.root/'candidates'))
        self.environment.start()
    def tearDown(self):
        self.environment.stop();self.temp.cleanup()
    def tags(self,stars):
        return {'IFD0:Rating':stars,'IFD0:RatingPercent':embedded.VALUES[stars],
                'XMP-xmp:Rating':stars,'XMP-microsoft:RatingPercent':embedded.VALUES[stars]}
    def simulate(self,arguments,timeout):
        target=Path(arguments[-1])
        if '-j' in arguments:
            return json.dumps([self.tags(3 if target!=self.original else 0)]).encode()
        self.assertNotEqual(target,self.original)
        self.assertIn(self.root/'candidates',target.parents)
        self.assertEqual(target.read_bytes(),b'original RAW payload')
        target.write_bytes(b'verified RAW plus stars')
        return b'1 image files updated'
    def test_verified_candidate_commits_only_after_child_finishes(self):
        with patch.object(embedded,'_run',side_effect=self.simulate):
            result=embedded.write(self.original,3)
        self.assertEqual(result,{'stars':3,'before':0})
        self.assertEqual(self.original.read_bytes(),b'verified RAW plus stars')
        self.assertEqual(list((self.root/'candidates').iterdir()),[])
    def test_timeout_leaves_original_untouched(self):
        def run(arguments,timeout):
            if '-j' in arguments:return b'[{}]'
            target=Path(arguments[-1]);self.assertNotEqual(target,self.original)
            target.write_bytes(b'partially written candidate')
            raise embedded.EmbeddedRatingError('timeout')
        with patch.object(embedded,'_run',side_effect=run):
            with self.assertRaises(embedded.EmbeddedRatingError):embedded.write(self.original,3)
        self.assertEqual(self.original.read_bytes(),b'original RAW payload')
    def test_rejects_failed_four_tag_verification(self):
        with patch.object(embedded,'_run',return_value=b'[{}]'):
            with self.assertRaisesRegex(embedded.EmbeddedRatingError,'四项'):embedded.write(self.original,3)
        self.assertEqual(self.original.read_bytes(),b'original RAW payload')
    def test_external_original_change_is_not_overwritten(self):
        def run(arguments,timeout):
            if '-j' in arguments:return json.dumps([self.tags(3 if Path(arguments[-1])!=self.original else 0)]).encode()
            self.original.write_bytes(b'external edit');return b''
        with patch.object(embedded,'_run',side_effect=run):
            with self.assertRaisesRegex(embedded.EmbeddedRatingError,'已改变'):embedded.write(self.original,3)
        self.assertEqual(self.original.read_bytes(),b'external edit')
    def test_undo_refuses_conflicting_external_stars(self):
        with patch.object(embedded,'_run',return_value=json.dumps([self.tags(4)]).encode()):
            with self.assertRaisesRegex(embedded.EmbeddedRatingError,'外部修改'):embedded.write(self.original,2,expected=3)
        self.assertFalse((self.root/'candidates').exists())
    def test_prefers_standard_xmp_to_stale_exif(self):
        with patch.object(embedded,'_run',return_value=b'[{"XMP-xmp:Rating":4,"IFD0:Rating":2}]'):
            self.assertEqual(embedded.read(self.original)['stars'],4)
    def test_percent_only_and_unrated(self):
        for payload,expected in ((b'[{"IFD0:RatingPercent":75}]',4),(b'[{}]',0)):
            with patch.object(embedded,'_run',return_value=payload):self.assertEqual(embedded.read(self.original)['stars'],expected)
    def test_rejects_invalid_stars_and_malformed_json(self):
        for value in (True,-1,6,'3'):
            with self.assertRaises(embedded.EmbeddedRatingError):embedded.write(self.original,value)
        with patch.object(embedded,'_run',return_value=b'not json'):
            with self.assertRaises(embedded.EmbeddedRatingError):embedded.read(self.original)
    def test_unicode_path_uses_utf8_argument_input_without_lossy_command_line(self):
        result=subprocess.CompletedProcess([],0,b'[{}]',b'')
        with patch.object(embedded,'executable',return_value='exiftool'),patch.object(embedded.subprocess,'run',return_value=result) as run:
            self.assertEqual(embedded.read(self.original)['stars'],0)
        arguments=run.call_args.args[0];payload=run.call_args.kwargs['input'].decode('utf8')
        self.assertEqual(arguments,['exiftool','-charset','filename=UTF8','-@','-'])
        self.assertIn(str(self.original),payload);self.assertIn('\n--\n',payload)
    def test_argument_file_rejects_newline_in_path(self):
        with patch.object(embedded.subprocess,'run') as run:
            with self.assertRaisesRegex(embedded.EmbeddedRatingError,'换行符'):embedded.read('bad\n-overwrite_original.ARW')
        run.assert_not_called()
    def test_subprocess_timeout_uses_safe_component_error(self):
        with patch.object(embedded,'executable',return_value='exiftool'),patch.object(embedded.subprocess,'run',side_effect=subprocess.TimeoutExpired('exiftool',1)):
            with self.assertRaisesRegex(embedded.EmbeddedRatingError,'超时'):embedded._run(['--',self.original],1)


class NativeRoutingTests(unittest.TestCase):
    def operation(self,stars,shell):
        fake_os=type('OS',(),{'name':'nt','W_OK':os.W_OK,'access':staticmethod(lambda *args:True)})
        with patch.object(native,'os',fake_os),patch.object(embedded,'executable',return_value='exiftool'),patch.object(embedded,'read',return_value={'stars':stars}),patch.object(native,'_operate_windows',side_effect=shell if isinstance(shell,Exception) else None,return_value=shell):
            return native.operate('test.ARW')
    def test_windows_visibility_is_independently_verified(self):
        result=self.operation(3,{'stars':3})
        self.assertEqual(result['backend'],'embedded');self.assertIs(result['windows_visible'],True)
    def test_mismatching_shell_is_not_reported_as_system_rating(self):
        result=self.operation(3,{'stars':0})
        self.assertEqual(result['stars'],3);self.assertIs(result['windows_visible'],False)
    def test_missing_raw_handler_still_reads_file_stars(self):
        result=self.operation(3,native.RatingError('no handler'))
        self.assertEqual(result['stars'],3);self.assertIs(result['windows_visible'],False)
    def test_zero_stars_cannot_prove_windows_raw_support(self):
        self.assertIsNone(self.operation(0,{'stars':0})['windows_visible'])
    def test_non_raw_routes_to_existing_native_writer(self):
        with patch.object(native,'_operate_windows',return_value={'stars':2}) as operate:
            self.assertEqual(native.operate('test.jpg',2),{'stars':2})
            operate.assert_called_once_with('test.jpg',2,None)


if __name__=='__main__':unittest.main()
