import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from PySide6.QtCore import QCoreApplication,QSettings,QEvent,Qt
from PySide6.QtWidgets import QApplication
from core import i18n
from core.update_announcements import CURRENT_BUILD_ID,load_announcements,parse_announcements
from ui.update_announcements import UpdateAnnouncementsDialog
from ui.main_window import MainWindow
from tests.test_online_update_ui import FakeUpdateService

APP=QApplication.instance() or QApplication([])

class OnlineAnnouncementTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory()
        QCoreApplication.setOrganizationName('OsuSkinEditorTests')
        QCoreApplication.setApplicationName('OnlineAnnouncements')
        QSettings.setDefaultFormat(QSettings.IniFormat)
        QSettings.setPath(QSettings.IniFormat,QSettings.UserScope,self.temp.name)
        QSettings().clear()
        i18n.load_language('zh-CN')
        self.settings=QSettings()
        self.local=load_announcements()
        self.remote=deepcopy(self.local[0])
        self.remote['id']='preview-r99'
        self.remote['title']['zh-CN']='远程公告 <img src="https://example.invalid/x">'
        self.dialog=UpdateAnnouncementsDialog(self.settings)
        self.dialog.show()
        APP.processEvents()

    def tearDown(self):
        self.dialog.close()
        self.dialog.deleteLater()
        QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete)
        APP.processEvents()
        self.temp.cleanup()

    def test_bundle_contains_only_current_build_and_feed_contains_history(self):
        self.assertEqual([e['id'] for e in self.local],[CURRENT_BUILD_ID])
        feed=Path(__file__).resolve().parents[1]/'updates/announcements.json'
        self.assertGreater(len(parse_announcements(feed.read_bytes())),1)

    def test_online_refresh_does_not_replace_local_version_and_escapes_markup(self):
        calls=[]
        self.dialog.refresh_requested.connect(calls.append)
        self.dialog.set_online_entries((self.remote,*self.local))
        self.assertEqual(self.dialog.entries,self.local)
        self.dialog.source_tabs.setCurrentIndex(1)
        self.assertEqual(calls,[False])
        self.dialog.version_list.setCurrentRow(0)
        self.assertIn('<img src=',self.dialog.body.toPlainText())
        self.assertIn('&lt;img',self.dialog.body.toHtml())
        self.assertFalse(self.dialog.body.openExternalLinks())
        self.dialog.refresh_button.click()
        self.assertEqual(calls,[False,True])
        self.dialog.source_tabs.setCurrentIndex(0)
        self.assertEqual(self.dialog.entries,self.local)

    def test_failed_online_request_keeps_offline_tab_available(self):
        self.dialog.set_online_entries((self.remote,))
        self.dialog.source_tabs.setCurrentIndex(1)
        self.dialog.set_online_error('timeout')
        self.assertEqual(self.dialog.entries,())
        self.assertIn('timeout',self.dialog.body.toPlainText())
        self.dialog.source_tabs.setCurrentIndex(0)
        self.assertEqual(self.dialog.entries,self.local)

    def test_auto_notice_respects_current_build_channel_and_ignores_old_selector_setting(self):
        with patch('ui.main_window.UpdateService',FakeUpdateService):
            window=MainWindow()
        window.show()
        APP.processEvents()
        window.settings.setValue('updates/channel','preview')
        with patch('ui.main_window.CHANNEL','stable'):
            window._online_notes_ready((self.remote,*self.local))
        self.assertIsNone(window._pending_online_notice)
        window.settings.setValue('updates/channel','stable')
        with patch('ui.main_window.CHANNEL','preview'):
            window._online_notes_ready((self.remote,*self.local))
        self.assertEqual(window._pending_online_notice,self.remote['id'])
        window._maybe_show_update_announcement()
        viewer=window._update_dialog
        self.assertEqual(viewer.source_tabs.currentIndex(),1)
        self.assertEqual(viewer.version_list.currentItem().data(Qt.UserRole),self.remote['id'])
        window.close()
        window.deleteLater()
        QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete)

if __name__=='__main__':
    unittest.main()
