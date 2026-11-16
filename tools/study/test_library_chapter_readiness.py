"""Actual raster PDFs test visual reading without OCR or fake teaching claims."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from PIL import Image, ImageDraw
from library import Library
from learning_library.versions import sha
from chapter_readiness import reading_route


class ReadingRouteTests(unittest.TestCase):
    def fixture(self, blank=False):
        temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(temp.cleanup)
        study = Path(temp.name) / 'Study'
        source = study / 'C/scan.pdf'
        source.parent.mkdir(parents=True)
        pages = []
        for n in range(2):
            image = Image.new('RGB', (400, 400), 'white')
            if not blank:
                ImageDraw.Draw(image).text((35, 50), 'PV factor 0.90909 - page '+str(n+1), fill='black')
            pages.append(image)
        pages[0].save(source, save_all=True, append_images=pages[1:])
        path = study / '_System/state.json'; path.parent.mkdir()
        path.write_text(json.dumps({'revision':1,'courses':[{'id':'C'}], 'settings':{},
            'materials':[{'id':'SCAN','course_id':'C','filename':'scan.pdf','relative_path':'C/scan.pdf','sha256':sha(source)}]}))
        lib = Library(path); lib.sync('C')
        return lib, source

    def test_actual_scan_is_synced_but_all_pages_still_require_visual_reading(self):
        lib, source = self.fixture(); before = lib.state_path.read_bytes() if hasattr(lib,'state_path') else None
        result = reading_route(lib, 'SCAN')
        self.assertEqual(result['reading_mode'], 'visual')
        self.assertEqual(result['visual_units'], [1,2])
        self.assertFalse(result['reviewed']); self.assertFalse(result['mastery_updated'])
        self.assertEqual(reading_route(lib, 'SCAN'), result)

    def test_render_failure_is_not_synchronization_success(self):
        lib, _ = self.fixture()
        with patch.object(lib, 'render', side_effect=ValueError('render unavailable')):
            with self.assertRaises(ValueError): reading_route(lib, 'SCAN')

    def test_blank_or_modified_original_is_not_a_readable_scan(self):
        lib, source = self.fixture(blank=True)
        with self.assertRaises(ValueError): reading_route(lib, 'SCAN')
        source.write_bytes(b'%PDF damaged')
        with self.assertRaises(ValueError): reading_route(lib, 'SCAN')

if __name__ == '__main__': unittest.main()
