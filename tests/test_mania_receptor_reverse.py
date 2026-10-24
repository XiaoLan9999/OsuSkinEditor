"""Signed receptor changes survive export without changing scale or crop end."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from PIL import Image
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QApplication

from core.mania_designer import (
    DesignValidationError, ManiaDesign, effective_hit_position, export_skin, validate_design,
)
from core.skin_ini import SkinIni
from core.skin_loader import SkinLoader
from ui.preview.mania_preview import ManiaPreview


APP = QApplication.instance() or QApplication([])


class _ReverseFixture:
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.source = self.base/'source'
        self.source.mkdir()
        self.write_ini()

    def write_ini(self, extra=''):
        (self.source/'skin.ini').write_text(
            '[General]\nVersion: 2.7\nName: Reverse fixture\n'
            '[Mania]\nKeys: 1\nHitPosition: 425\nColumnWidth: 50\n'
            'KeyImage0: keys/up\nKeyImage0D: keys/down\n'+extra,
            encoding='utf-8')

    def keys(self, density=1, height=160):
        for state, offset in (('up', 0), ('down', 70)):
            image = Image.new('RGBA', (12*density, height))
            image.putdata([((x*13+offset)%256, y%256, (y*7+offset)%256,
                            70+(x+y)%180) for y in range(height) for x in range(image.width)])
            path = self.source/f'keys/{state}{"@2x" if density==2 else ""}.png'
            path.parent.mkdir(exist_ok=True)
            image.save(path)

    def hashes(self):
        return {p.relative_to(self.source):sha256(p.read_bytes()).hexdigest()
                for p in self.source.rglob('*') if p.is_file()}


class ReverseReceptorTests(_ReverseFixture,unittest.TestCase):
    def test_downward_export_crops_only_bottom_and_links_target(self):
        self.keys()
        before = self.hashes()
        result = export_skin(self.source, 1, self.base/'copy', ManiaDesign(receptor_raise=-10))
        data = SkinIni.read(result.root/'skin.ini').mania_get(1)
        self.assertEqual(data['HitPosition'], '435')
        for option, state in (('KeyImage0','up'), ('KeyImage0D','down')):
            with Image.open(self.source/f'keys/{state}.png') as original, \
                    Image.open(result.root/(data[option]+'.png')) as adjusted:
                self.assertEqual(adjusted.size, (12,144))
                self.assertEqual(adjusted.tobytes(), original.crop((0,0,12,144)).tobytes())
        self.assertEqual(self.hashes(), before)
        self.assertNotIn('StageBottom',data)

    def test_downward_unlinked_keeps_hitposition_and_foreground_reference(self):
        self.keys()
        self.write_ini('StageBottom: existing-footer\n')
        result = export_skin(self.source,1,self.base/'copy',
                             ManiaDesign(receptor_raise=-10,link_hit_position=False))
        data = SkinIni.read(result.root/'skin.ini').mania_get(1)
        self.assertEqual(data['HitPosition'],'425')
        self.assertEqual(data['StageBottom'],'existing-footer')

    def test_downward_link_respects_bottom_boundary_and_unlinked_mode(self):
        self.assertEqual(effective_hit_position({'HitPosition':'425'},ManiaDesign(receptor_raise=-55)),480)
        with self.assertRaises(DesignValidationError):
            effective_hit_position({'HitPosition':'425'},ManiaDesign(receptor_raise=-56))
        self.assertEqual(effective_hit_position({'HitPosition':'425'},
                         ManiaDesign(receptor_raise=-240,link_hit_position=False)),425)
        for value in (-241,241,float('nan'),float('inf')):
            with self.subTest(value=value), self.assertRaises(DesignValidationError):
                validate_design(ManiaDesign(receptor_raise=value))

    def test_hd_only_export_preserves_native_pixels_and_sd_fallback_policy(self):
        self.keys(density=2,height=321)
        before=self.hashes()
        result=export_skin(self.source,1,self.base/'copy',ManiaDesign(receptor_raise=-1))
        data=SkinIni.read(result.root/'skin.ini').mania_get(1)
        with Image.open(self.source/'keys/up@2x.png') as original, \
                Image.open(result.root/(data['KeyImage0']+'@2x.png')) as hd, \
                Image.open(result.root/(data['KeyImage0']+'.png')) as sd:
            expected=original.crop((0,0,24,318))
            self.assertEqual(hd.size,(24,318))
            self.assertEqual(hd.tobytes(),expected.tobytes())
            self.assertEqual(sd.size,(12,159))
            self.assertEqual(sd.tobytes(),expected.resize((12,159),Image.Resampling.LANCZOS).tobytes())
        self.assertEqual(self.hashes(),before)

    def test_cropping_entire_key_rejects_before_creating_copy(self):
        self.keys(height=16)
        before=self.hashes()
        destination=self.base/'copy'
        with self.assertRaisesRegex(DesignValidationError,'entire key image'):
            export_skin(self.source,1,destination,ManiaDesign(receptor_raise=-10))
        self.assertFalse(destination.exists())
        self.assertFalse(list(self.base.glob('.mania-design-*')))
        self.assertEqual(self.hashes(),before)

    def test_single_remaining_row_is_preserved(self):
        self.keys(height=16)
        result=export_skin(self.source,1,self.base/'copy',ManiaDesign(receptor_raise=-9.375))
        data=SkinIni.read(result.root/'skin.ini').mania_get(1)
        with Image.open(self.source/'keys/up.png') as original, \
                Image.open(result.root/(data['KeyImage0']+'.png')) as adjusted:
            self.assertEqual(adjusted.size,(12,1))
            self.assertEqual(adjusted.tobytes(),original.crop((0,0,12,1)).tobytes())


class ReverseReceptorPreviewTests(_ReverseFixture,unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.previews=[]

    def tearDown(self):
        for preview in self.previews:
            preview.close()
            preview.deleteLater()
        QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete)
        APP.processEvents()

    def preview(self,root):
        preview=ManiaPreview()
        preview.resize(856,552)
        preview.set_playing(False)
        preview.set_skin(SkinLoader().load(str(root)))
        self.previews.append(preview)
        return preview

    @staticmethod
    def render_keys(preview,pressed=False):
        preview.game.pressed_lanes={0} if pressed else set()
        image=QImage(preview.width(),preview.height(),QImage.Format_RGBA8888)
        image.fill(Qt.transparent)
        painter=QPainter(image)
        field,scale,widths,spacing,left,_=preview._geometry()
        painter.setClipRect(field)
        if preview._bool(preview._settings.get('upsidedown')):
            painter.translate(0,field.top()+field.bottom())
            painter.scale(1,-1)
        preview._draw_keys(painter,field,scale,widths,spacing,left)
        painter.end()
        return image

    def test_preview_matches_saved_copy_for_sd_hd_pressed_and_flipped_keys(self):
        for density in (1,2):
            for upside in (0,1):
                for flip in (0,1):
                    with self.subTest(density=density,upside=upside,flip=flip):
                        self.keys(density=density,height=160*density)
                        # Remove previous density variants so each subtest uses
                        # the intended texture, rather than an old HD priority.
                        other='' if density==2 else '@2x'
                        for state in ('up','down'):
                            (self.source/f'keys/{state}{other}.png').unlink(missing_ok=True)
                        self.write_ini(f'UpsideDown: {upside}\nKeyFlipWhenUpsideDown0: {flip}\n'
                                       f'KeyFlipWhenUpsideDown0D: {1-flip}\n')
                        pending=self.preview(self.source)
                        design=ManiaDesign(receptor_raise=-0.625)
                        pending.set_design_options(design)
                        destination=self.base/f'copy-{density}-{upside}-{flip}'
                        saved=export_skin(self.source,1,destination,design)
                        reopened=self.preview(saved.root)
                        for pressed in (False,True):
                            image=self.render_keys(pending,pressed)
                            expected=self.render_keys(reopened,pressed)
                            self.assertFalse(image.isNull())
                            self.assertEqual(image.bits().tobytes(),expected.bits().tobytes())

    def test_preview_rejects_full_crop_without_replacing_previous_valid_design(self):
        self.keys(height=16)
        preview=self.preview(self.source)
        valid=ManiaDesign(receptor_raise=1)
        preview.set_design_options(valid)
        with self.assertRaisesRegex(DesignValidationError,'entire key image'):
            preview.set_design_options(ManiaDesign(receptor_raise=-10))
        self.assertEqual(preview.design_options,valid)

    def test_reverse_crop_is_reused_until_design_or_skin_changes(self):
        self.keys(height=160)
        preview=self.preview(self.source)
        preview.set_design_options(ManiaDesign(receptor_raise=-10))
        self.render_keys(preview)
        first={key:value.cacheKey() for key,value in preview._receptor_crop_cache.items()}
        self.assertTrue(first)
        self.render_keys(preview)
        self.assertEqual(first,{key:value.cacheKey() for key,value in preview._receptor_crop_cache.items()})
        preview.set_design_options(ManiaDesign(receptor_raise=-9))
        self.assertEqual(preview._receptor_crop_cache,{})
        self.render_keys(preview)
        preview.set_skin(SkinLoader().load(str(self.source)))
        self.assertEqual(preview._receptor_crop_cache,{})


if __name__=='__main__':
    unittest.main()
