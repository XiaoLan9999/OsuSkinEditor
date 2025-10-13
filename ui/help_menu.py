# -*- coding: utf-8 -*-
from PySide6.QtWidgets import QAction, QMessageBox
from PySide6.QtGui import QDesktopServices
from PySide6.QtCore import QUrl
from core.app_links import get_links
from core import i18n

def _open(url: str, parent):
    if not url:
        QMessageBox.information(parent, "Links", i18n.t("links.configure", "请在 core/app_links.py 填写链接后再试。"))
        return
    QDesktopServices.openUrl(QUrl(url))

def install_help_menu(win):
    """Add a tail 'Links/链接' menu with 3 items: GitHub, Author, Blog.
    Usage in MainWindow.__init__:
        from ui.help_menu import install_help_menu
        install_help_menu(self)
    """
    menu = win.menuBar().addMenu(i18n.t("menu.links", "链接"))
    L = get_links()
    act_git   = QAction(i18n.t("links.github", "GitHub 项目地址"), win)
    act_auth  = QAction(i18n.t("links.author", "作者主页"), win)
    act_blog  = QAction(i18n.t("links.blog", "博客地址"), win)
    act_git.triggered.connect(lambda: _open(L.get("github"), win))
    act_auth.triggered.connect(lambda: _open(L.get("author"), win))
    act_blog.triggered.connect(lambda: _open(L.get("blog"), win))
    menu.addAction(act_git); menu.addAction(act_auth); menu.addAction(act_blog)
    return menu
