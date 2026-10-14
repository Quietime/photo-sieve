import hashlib,json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image
from photo_metadata import shooting_info,format_info

class MetadataTest(unittest.TestCase):
    def test_jpeg_read_is_complete_and_leaves_file_unchanged(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'photo.jpg';exif=Image.Exif()
            exif[271]='Sony';exif[272]='ILCE-7M4';exif[34665]={33437:2.8,33434:1/160,34855:800,36867:'2026:10:14 12:30:00',37386:85,42036:'FE 85mm F1.8'}
            Image.new('RGB',(50,60)).save(path,exif=exif);before=hashlib.sha256(path.read_bytes()).digest()
            result=shooting_info(path)
            for value in ['Sony ILCE-7M4','FE 85mm F1.8','85 mm','f/2.8','1/160s','ISO 800','2026-10-14 12:30:00']:self.assertIn(value,result)
            self.assertEqual(hashlib.sha256(path.read_bytes()).digest(),before)
    def test_no_exif_does_not_fabricate_camera_or_shooting_time(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'photo.jpg';Image.new('RGB',(30,30)).save(path);os.utime(path,(1,1))
            self.assertEqual(shooting_info(path),'')
    def test_raw_component_reads_new_fields_and_omits_unknown_lens(self):
        row={'Make':'SONY','Model':'ILCE-7S','LensModel':'----','ISO':400,'FocalLength':35,'ExposureTime':1/250,'DateTimeOriginal':'2017:07:13 16:58:28'}
        with patch('embedded_rating._run',return_value=json.dumps([row]).encode()) as command:
            result=shooting_info('camera.ARW')
        self.assertIn('SONY ILCE-7S',result);self.assertIn('35 mm',result);self.assertNotIn('----',result)
        self.assertIn('-LensModel',command.call_args.args[0]);self.assertIn('-FocalLength',command.call_args.args[0])
    def test_camera_brand_is_not_duplicated_and_missing_fields_are_hidden(self):
        self.assertEqual(format_info(make='Sony',model='Sony ILCE-7M4',lens='Unknown'),'Sony ILCE-7M4')

if __name__=='__main__':unittest.main()
