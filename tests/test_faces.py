"""Face crops preserve original pixels, obey image boundaries and carry honest scale metadata."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw

import face_engine


class FaceCropTest(unittest.TestCase):
    def test_crop_reads_original_pixels_with_context_instead_of_reusing_preview(self):
        original = Image.new('RGB', (800, 600), 'blue')
        ImageDraw.Draw(original).rectangle((320, 180, 399, 299), fill='red')
        face = {'box': [.4, .3, .1, .2], 'score': .96}
        result = face_engine.crop_faces(original, [face])
        crop = result['image']
        self.assertEqual(result['crop_box'], [300, 150, 420, 330])
        self.assertEqual(crop.size, (120, 180))
        self.assertEqual(crop.getpixel((0, 0)), (0, 0, 255), 'The surrounding original scene is included')
        self.assertEqual(crop.getpixel((60, 90)), (255, 0, 0), 'The face region comes from the native original')
        self.assertEqual(crop.tobytes(), original.crop(tuple(result['crop_box'])).tobytes())
        self.assertEqual((result['ratio'], result['ratio_x'], result['ratio_y']), (1, 1, 1))
        self.assertEqual(result['native_size'], result['output_size'])
        self.assertEqual(result['original_size'], [800, 600])

    def test_largest_face_wins_even_if_supplied_order_or_area_is_wrong(self):
        original = Image.new('RGB', (1000, 800), 'black')
        draw = ImageDraw.Draw(original)
        draw.rectangle((100, 80, 149, 119), fill='red')
        draw.rectangle((400, 160, 699, 479), fill='green')
        small = {'box': [.1, .1, .05, .05], 'score': .99, 'area': 1000}
        large = {'box': [.4, .2, .3, .4], 'score': .85, 'area': 0}
        result = face_engine.crop_faces(original, [small, large], padding=0)
        self.assertEqual(result['face_count'], 2)
        self.assertEqual(result['face']['score'], .85)
        self.assertEqual(result['image'].getpixel((150, 150)), (0, 128, 0))
        self.assertEqual(result['image'].size, (300, 320))

    def test_face_at_boundary_is_clipped_without_black_padding_or_wraparound(self):
        original = Image.new('RGB', (400, 300), 'orange')
        result = face_engine.crop_faces(original, [{'box': [-.1, -.1, .25, .3]}])
        self.assertEqual(result['crop_box'][:2], [0, 0])
        self.assertGreater(result['crop_box'][2], 0)
        self.assertGreater(result['crop_box'][3], 0)
        self.assertLessEqual(result['crop_box'][2], 400)
        self.assertLessEqual(result['crop_box'][3], 300)
        self.assertEqual(result['image'].getextrema(), ((255, 255), (165, 165), (0, 0)))
        self.assertEqual(result['ratio'], 1)

    def test_lower_right_face_is_clipped_to_original_size(self):
        original = Image.new('RGB', (401, 299), 'white')
        result = face_engine.crop_faces(original, [{'box': [.9, .85, .3, .3]}])
        self.assertEqual(result['crop_box'][2:], [401, 299])
        self.assertGreater(result['image'].width, 0)
        self.assertGreater(result['image'].height, 0)
        self.assertEqual(result['image'].getextrema(), ((255, 255), (255, 255), (255, 255)))

    def test_large_crop_is_bounded_and_keeps_original_high_frequency_pixels(self):
        original = Image.new('RGB', (4000, 3000), 'black')
        draw = ImageDraw.Draw(original)
        for x in range(0, 4000, 2):
            draw.line((x, 0, x, 2999), fill='white')
        result = face_engine.crop_faces(original, [{'box': [.1, .1, .8, .8]}])
        self.assertLessEqual(max(result['image'].size), 1024)
        self.assertEqual(result['requested_box'], [0, 0, 4000, 3000])
        self.assertEqual(result['ratio'], 1)
        self.assertEqual(result['ratio_x'], 1)
        self.assertEqual(result['ratio_y'], 1)
        self.assertTrue(result['is_partial'])
        self.assertEqual(result['native_size'], result['output_size'])
        self.assertEqual(result['output_size'], list(result['image'].size))
        self.assertEqual(result['image'].tobytes(), original.crop(tuple(result['crop_box'])).tobytes())
        self.assertNotEqual(result['image'].getpixel((100, 100)), result['image'].getpixel((101, 100)),
                            'One-pixel focus detail must not turn into averaged resampled pixels')

    def test_partial_crop_prefers_eyes_to_lower_face_center(self):
        original = Image.new('RGB', (3000, 3000), 'blue')
        face = {'box': [.2, .2, .6, .6], 'landmarks': [[.38, .35], [.62, .35], [.5, .5], [.4, .7], [.6, .7]]}
        result = face_engine.crop_faces(original, [face])
        left, top, right, bottom = result['crop_box']
        self.assertAlmostEqual((left + right) / 2, 1500)
        self.assertAlmostEqual((top + bottom) / 2, 1050)
        self.assertEqual(result['image'].size, (1024, 1024))
        self.assertTrue(result['is_partial'])
        self.assertEqual(result['ratio'], 1)

    def test_partial_crop_with_invalid_eye_points_uses_face_center(self):
        original = Image.new('RGB', (3000, 3000), 'blue')
        face = {'box': [.2, .2, .6, .6], 'landmarks': [[float('nan'), .1], [.9, .1]]}
        result = face_engine.crop_faces(original, [face])
        left, top, right, bottom = result['crop_box']
        self.assertEqual(((left + right) / 2, (top + bottom) / 2), (1500, 1500))
        self.assertEqual(result['ratio'], 1)

    def test_partial_crop_preserves_full_short_dimension_and_clamps_to_face_context(self):
        original = Image.new('RGB', (3000, 1000), 'cyan')
        face = {'box': [0, .2, .8, .2], 'landmarks': [[.01, .25], [.03, .25]]}
        result = face_engine.crop_faces(original, [face])
        self.assertEqual(result['crop_box'], [0, 150, 1024, 450])
        self.assertEqual(result['image'].size, (1024, 300))
        self.assertTrue(result['is_partial'])

    def test_small_face_is_never_upscaled(self):
        original = Image.new('RGB', (1000, 800), 'cyan')
        result = face_engine.crop_faces(original, [{'box': [.3, .3, .02, .025]}])
        self.assertLess(max(result['image'].size), 40)
        self.assertEqual(result['ratio'], 1)
        self.assertEqual(result['native_size'], result['output_size'])
        self.assertFalse(result['is_partial'])

    def test_absent_faces_have_explicit_empty_metadata_and_do_not_crop(self):
        original = Image.new('RGB', (80, 60), 'blue')
        with patch.object(original, 'crop', side_effect=AssertionError('An empty scene must not allocate a crop')):
            result = face_engine.crop_faces(original, [])
        self.assertIsNone(result['image'])
        self.assertEqual(result['face_count'], 0)
        self.assertIsNone(result['crop_box'])
        self.assertIsNone(result['ratio'])
        json.dumps(result)

    def test_invalid_or_outside_cached_boxes_are_ignored(self):
        original = Image.new('RGB', (100, 100), 'blue')
        invalid = [{}, None, {'box': [0, 0, 0, .2]}, {'box': [0, 0, .2, -.1]},
                   {'box': [float('nan'), 0, .3, .3]}, {'box': [0, 0, float('inf'), .2]},
                   {'box': [2, 2, .2, .2]}, {'box': [0, 0]}, {'box': 'broken'}]
        result = face_engine.crop_faces(original, invalid)
        self.assertIsNone(result['image'])
        self.assertEqual(result['face_count'], 0)

    def test_metadata_round_trips_through_json_without_image_or_input_mutation(self):
        original = Image.new('RGB', (200, 100), 'blue')
        faces = [{'box': [.25, .2, .2, .4], 'score': .97, 'landmarks': [[.3, .3], [.4, .3]]}]
        before = json.dumps(faces)
        result = face_engine.crop_faces(original, faces)
        metadata = {key: value for key, value in result.items() if key != 'image'}
        self.assertEqual(json.loads(json.dumps(metadata)), metadata)
        self.assertEqual(json.dumps(faces), before)
        self.assertEqual(metadata['engine_version'], face_engine.ENGINE_VERSION)

    def test_invalid_limits_fail_instead_of_upscaling_or_allocating_unbounded_crop(self):
        original = Image.new('RGB', (100, 100))
        for arguments in ({'padding': -1}, {'padding': float('nan')}, {'max_edge': 0}, {'max_edge': 2.5}):
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                face_engine.crop_faces(original, [{'box': [.2, .2, .2, .2]}], **arguments)


class LocalDetectionTest(unittest.TestCase):
    def test_missing_local_model_is_distinct_from_a_scene_with_no_faces(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(face_engine.FaceDetectionUnavailable, '本地人脸检测模型'):
                face_engine.detect_faces(Image.new('RGB', (80, 60)), model_path=Path(directory) / 'missing.onnx')

    @unittest.skipUnless(face_engine.MODEL_PATH.is_file(), 'The bundled YuNet model is not available')
    def test_real_local_detector_returns_no_faces_for_plain_scene_without_network(self):
        import socket
        with patch.object(socket, 'create_connection', side_effect=AssertionError('Local face detection must never call the network')):
            self.assertEqual(face_engine.detect_faces(Image.new('RGB', (641, 423), 'grey')), [])

    @unittest.skipUnless(os.environ.get('PHOTOSIEVE_FACE_SAMPLE'), 'Set PHOTOSIEVE_FACE_SAMPLE to an external portrait fixture')
    def test_real_portrait_and_two_person_scene_have_normalized_json_metadata(self):
        # No reference photograph is bundled in the source or the application.
        with Image.open(os.environ['PHOTOSIEVE_FACE_SAMPLE']) as source:
            image = source.convert('RGB')
        faces = face_engine.detect_faces(image)
        self.assertGreaterEqual(len(faces), 1)
        scene = Image.new('RGB', (image.width * 2, image.height), 'white')
        scene.paste(image, (0, 0))
        scene.paste(image, (image.width, 0))
        two = face_engine.detect_faces(scene)
        self.assertEqual(len(two), 2)
        self.assertEqual(json.loads(json.dumps(two)), two)
        self.assertGreaterEqual(two[0]['area'], two[1]['area'])
        for face in two:
            self.assertTrue(all(0 <= value <= 1 for value in face['box']))
            self.assertEqual(len(face['landmarks']), 5)
            self.assertTrue(all(0 <= value <= 1 for point in face['landmarks'] for value in point))
            self.assertGreaterEqual(face['score'], .75)
            self.assertEqual(face['preview_size'], list(scene.size))
        self.assertNotEqual(two[0]['box'][0] > .5, two[1]['box'][0] > .5)
        result = face_engine.crop_faces(image, faces)
        self.assertEqual(result['ratio'], 1)
        self.assertLessEqual(max(result['image'].size), 1024)
        self.assertEqual(result['image'].tobytes(), image.crop(tuple(result['crop_box'])).tobytes())


if __name__ == '__main__':
    unittest.main()
