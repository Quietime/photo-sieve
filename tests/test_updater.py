import hashlib
import io
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile
from urllib.error import HTTPError
import updater
from version import APP_VERSION, UPDATE_REPOSITORY, UPDATE_ASSET


def installation(root, contents, version='3.0.0'):
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    for name,data in contents.items():
        path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
    (root/'installed-files.json').write_text(json.dumps({'version':version,'files':{name:hashlib.sha256(data).hexdigest() for name,data in contents.items()}}),encoding='utf-8')
    return root


class UpdaterTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.base=Path(self.temp.name)
        self.old=installation(self.base/'app',{'PhotoSieve.exe':b'old', '_internal/old.dll':b'old DLL'},'2.0.0')
        self.new=installation(self.base/'stage'/'new'/'PhotoSieve',{'PhotoSieve.exe':b'new','_internal/new.dll':b'new DLL'})
    def tearDown(self):self.temp.cleanup()
    @unittest.skipUnless(updater.os.name=='nt','Windows credential manager')
    def test_windows_credentials_round_trip_and_delete(self):
        import uuid
        with patch('updater.CREDENTIAL','PhotoSieve:test:'+uuid.uuid4().hex):
            try:
                self.assertEqual(updater.credential_store(),'')
                updater.credential_store('test-token-for-vault-round-trip')
                self.assertEqual(updater.credential_store(),'test-token-for-vault-round-trip')
                updater.credential_store(delete=True)
                self.assertEqual(updater.credential_store(),'')
            finally:updater.credential_store(delete=True)
    def test_install_preserves_photos_records_and_unknown_files(self):
        for name in ['photos/image.jpg','library.sqlite3','.photo-sieve-trash/deleted.raw','notes.txt']:
            path=self.old/name;path.parent.mkdir(exist_ok=True,parents=True);path.write_bytes(b'user data')
        updater.install_transaction(self.new,self.old,self.base/'backup')
        self.assertEqual((self.old/'PhotoSieve.exe').read_bytes(),b'new')
        self.assertFalse((self.old/'_internal/old.dll').exists())
        self.assertEqual((self.base/'backup/PhotoSieve.exe').read_bytes(),b'old')
        for name in ['photos/image.jpg','library.sqlite3','.photo-sieve-trash/deleted.raw','notes.txt']:
            self.assertEqual((self.old/name).read_bytes(),b'user data')
    def test_rollback_when_copy_fails_after_partial_write(self):
        original=shutil.copyfile;count=0
        def fail(source,destination):
            nonlocal count
            count+=1
            if count==2:raise OSError('disk full')
            return original(source,destination)
        with patch('updater.shutil.copyfile',side_effect=fail):
            with self.assertRaisesRegex(OSError,'disk full'):updater.install_transaction(self.new,self.old,self.base/'backup')
        self.assertEqual((self.old/'PhotoSieve.exe').read_bytes(),b'old')
        self.assertEqual((self.old/'_internal/old.dll').read_bytes(),b'old DLL')
        self.assertFalse((self.old/'_internal/new.dll').exists())
        self.assertEqual(updater.validate_install(self.old)['version'],'2.0.0')
    def test_collision_aborts_before_touching_old_install(self):
        path=self.old/'_internal/new.dll';path.write_bytes(b'user file')
        with self.assertRaisesRegex(ValueError,'重名'):updater.install_transaction(self.new,self.old,self.base/'backup')
        self.assertEqual((self.old/'PhotoSieve.exe').read_bytes(),b'old')
        self.assertEqual(path.read_bytes(),b'user file')
    def test_tampered_new_file_does_not_modify_old_install(self):
        (self.new/'PhotoSieve.exe').write_bytes(b'tampered')
        with self.assertRaisesRegex(ValueError,'校验失败'):updater.install_transaction(self.new,self.old,self.base/'backup')
        self.assertEqual((self.old/'PhotoSieve.exe').read_bytes(),b'old')
    def test_symlink_in_target_is_rejected(self):
        target=self.old/'_internal';shutil.rmtree(target);outside=self.base/'outside';outside.mkdir()
        try:target.symlink_to(outside,target_is_directory=True)
        except OSError:self.skipTest('symlinks unavailable')
        if not updater.linked(target):self.skipTest('platform does not expose symlink metadata')
        with self.assertRaisesRegex(ValueError,'符号链接'):updater.install_transaction(self.new,self.old,self.base/'backup')
        self.assertEqual((self.old/'PhotoSieve.exe').read_bytes(),b'old')
    def test_reject_dangerous_zip_paths_and_case_duplicates(self):
        for index,name in enumerate(['../escape','PhotoSieve/../escape','PhotoSieve/C:evil','PhotoSieve/CON.txt','PhotoSieve/file.','PhotoSieve/a\\..\\bad']):
            archive=self.base/f'bad{index}.zip'
            with ZipFile(archive,'w') as bundle:bundle.writestr(name,b'bad')
            with self.assertRaises(ValueError):updater.unpack(archive,self.base/f'extract{index}')
        archive=self.base/'duplicate.zip'
        with ZipFile(archive,'w') as bundle:
            bundle.writestr('PhotoSieve/file',b'one');bundle.writestr('PhotoSieve/FILE',b'two')
        with self.assertRaisesRegex(ValueError,'重复'):updater.unpack(archive,self.base/'duplicate')
    def test_zip_requires_matching_file_hashes(self):
        archive=self.base/'good.zip'
        with ZipFile(archive,'w') as bundle:
            for path in self.new.rglob('*'):
                if path.is_file():bundle.write(path,'PhotoSieve/'+path.relative_to(self.new).as_posix())
        extracted=updater.unpack(archive,self.base/'extracted')
        self.assertEqual(updater.validate_install(extracted)['version'],'3.0.0')
    def test_version_order_and_reject_invalid_version(self):
        self.assertGreater(updater.version_tuple('3.0.10'),updater.version_tuple('3.0.9'))
        for bad in ['../3.0.0','v3.0.0','3.0.0-beta','3.0']:
            with self.assertRaises(ValueError):updater.version_tuple(bad)
    def test_public_api_requests_do_not_include_authorization(self):
        api=f'https://api.github.com/repos/{UPDATE_REPOSITORY}/releases/latest'
        with patch('updater.build_opener') as opener:
            updater.open_api(api,'')
            self.assertIsNone(opener.return_value.open.call_args.args[0].get_header('Authorization'))
    def test_api_auth_never_sent_to_redirect_host(self):
        api=f'https://api.github.com/repos/{UPDATE_REPOSITORY}/releases/assets/1'
        error=HTTPError(api,302,'redirect',{'Location':'https://release-assets.githubusercontent.com/a'},None)
        with patch('updater.build_opener') as opener,patch('updater.urlopen',return_value=io.BytesIO(b'data')) as external:
            opener.return_value.open.side_effect=error
            updater.open_api(api,'secret-not-in-artifact',True)
            self.assertEqual(opener.return_value.open.call_args.args[0].get_header('Authorization'),'Bearer secret-not-in-artifact')
            self.assertIsNone(external.call_args.args[0].get_header('Authorization'))
        with self.assertRaises(ValueError):updater.open_api('https://example.com/api','secret',True)
    def test_api_rejects_untrusted_redirect(self):
        api=f'https://api.github.com/repos/{UPDATE_REPOSITORY}/releases/assets/1'
        error=HTTPError(api,302,'redirect',{'Location':'https://example.com/steal'},None)
        with patch('updater.build_opener') as opener:
            opener.return_value.open.side_effect=error
            with self.assertRaises(ValueError):updater.open_api(api,'secret',True)
    def test_latest_equal_or_older_release_skipped(self):
        with patch('updater.read_json',return_value={'tag_name':'v'+APP_VERSION,'assets':[]}):self.assertIsNone(updater.check_release('token'))
        with patch('updater.read_json',return_value={'tag_name':'v2.0.0','assets':[]}):self.assertIsNone(updater.check_release('token'))
    def test_release_manifest_matches_version_repository_and_asset(self):
        metadata={'version':'99.0.0','repository':UPDATE_REPOSITORY,'asset':UPDATE_ASSET,'size':123,'sha256':'a'*64}
        release={'tag_name':'v99.0.0','html_url':'https://github.com/'+UPDATE_REPOSITORY+'/releases/tag/v99.0.0','assets':[
            {'name':UPDATE_ASSET,'size':123,'url':f'https://api.github.com/repos/{UPDATE_REPOSITORY}/releases/assets/1'},
            {'name':'update.json','url':f'https://api.github.com/repos/{UPDATE_REPOSITORY}/releases/assets/2'}]}
        with patch('updater.read_json',return_value=release),patch('updater.open_api',return_value=io.BytesIO(json.dumps(metadata).encode())):
            self.assertEqual(updater.check_release('token')['version'],'99.0.0')
        metadata['repository']='wrong/repo'
        with patch('updater.read_json',return_value=release),patch('updater.open_api',return_value=io.BytesIO(json.dumps(metadata).encode())):
            with self.assertRaises(ValueError):updater.check_release('token')
    def test_corrupt_download_is_removed(self):
        release={'version':'99.0.0','url':f'https://api.github.com/repos/{UPDATE_REPOSITORY}/releases/assets/1','size':3,'sha256':'0'*64}
        with patch.dict('os.environ',{'LOCALAPPDATA':str(self.base/'local')}),patch('updater.open_api',return_value=io.BytesIO(b'bad')):
            with self.assertRaisesRegex(ValueError,'校验失败'):updater.download_release(release,'token')
        self.assertFalse(list((self.base/'local/PhotoSieve/updates').iterdir()))

if __name__=='__main__':unittest.main()
