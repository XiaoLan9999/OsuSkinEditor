# -*- coding: utf-8 -*-
from PySide6.QtWidgets import QWidget
from PySide6.QtGui import QPainter, QPen, QPixmap, QColor, QImage, QLinearGradient, QFont
from PySide6.QtCore import Qt, QTimer, QRectF
from pathlib import Path
import math
from PIL import Image
from core import i18n

def _parse_rgb(val, default=(0, 255, 255)):
    if not val: return default
    s = str(val).replace(';', ',').strip()
    parts = [p for token in s.split(',') for p in token.strip().split() if p]
    ints = []
    for p in parts:
        try:
            v = int(p)
            if 0 <= v <= 255: ints.append(v)
        except Exception: pass
    if len(ints) >= 3: return (ints[0], ints[1], ints[2])
    return default

def _parse_bool(val, default=False):
    if val is None: return default
    s = str(val).strip().lower()
    if s in ("1","true","yes","on"): return True
    if s in ("0","false","no","off"): return False
    return default

def _alpha_center(pm: QPixmap, thresh: int = 10):
    if pm is None or pm.isNull(): return (0,0)
    img = pm.toImage().convertToFormat(QImage.Format_RGBA8888)
    w, h = img.width(), img.height()
    pixels = Image.frombuffer("RGBA", (w, h), img.constBits(), "raw", "RGBA", img.bytesPerLine(), 1)
    bounds = pixels.getchannel("A").point(lambda alpha: 255 if alpha > thresh else 0).getbbox()
    if bounds is None: return (0,0)
    left, top, right, bottom = bounds
    return (int(round((w-left-right)/2.0)), int(round((h-top-bottom)/2.0)))

class StdPreview(QWidget):
    def __init__(self):
        super().__init__()
        self.skin = None
        self.t = 0
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.setInterval(16)
        self._playing = True
        self.preview_scale = 1.0
        self._mouse_position = None
        self.setMouseTracking(True)
        self.setMinimumSize(360, 300)

        self.combo_color = (0, 255, 255)
        self.pm_circle=None; self.off_circle=(0,0)
        self.pm_overlay=None; self.off_overlay=(0,0)
        self.pm_approach=None; self.off_approach=(0,0)
        self.pm_digits=[None]*10; self.off_digits=[(0,0)]*10
        self.pm_circle_tinted=None; self.pm_approach_tinted=None; self.overlay_above_number=True
        self.pm_cursor=None; self.cursor_center=True; self.cursor_rotate=True

        self.approach_center_mode="image"  # "image" or "alpha"

        # user micro adjustments (per-skin)
        self.user_offsets={
            "hit_dx":0,"hit_dy":0,              # hitcircle only
            "ovl_dx":0,"ovl_dy":0,              # overlay relative to circle
            "num_dx":0,"num_dy":0,              # number (relative if link_num=1, absolute if 0)
            "link_num":1,                       # 1: number follows hitcircle; 0: independent
            "approach_dx":0,"approach_dy":0     # approach circle
        }

        # debug config (session only)
        self.debug_opts={
            "show_centers":False,   # draw cross at visual centers
            "sample_digit":6,       # 0..9
        }

    # ---------- config API ----------
    def set_preview_scale(self, scale: float):
        self.preview_scale = max(0.25, min(4.0, float(scale)))
        self.update()

    def mouseMoveEvent(self, event):
        self._mouse_position = event.position()
        self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        self._mouse_position = None
        self.update()
        super().leaveEvent(event)

    def set_playing(self, playing: bool):
        self._playing = bool(playing)
        if self._playing and self.isVisible():
            self.timer.start()
        else:
            self.timer.stop()

    def showEvent(self, event):
        super().showEvent(event)
        self.set_playing(self._playing)

    def hideEvent(self, event):
        self.timer.stop()
        super().hideEvent(event)

    def set_approach_center_mode(self, mode:str):
        mode=(mode or "").lower()
        if mode in ("image","alpha"):
            self.approach_center_mode=mode; self.update()

    def set_user_offsets(self, d:dict):
        if not isinstance(d, dict): return
        for k in ("hit_dx","hit_dy","ovl_dx","ovl_dy","num_dx","num_dy","approach_dx","approach_dy","link_num"):
            if k in d:
                try:
                    if k=="link_num":
                        self.user_offsets[k]=1 if str(d[k]).lower() in ("1","true","yes","on") else 0
                    else:
                        self.user_offsets[k]=int(d[k])
                except Exception: pass
        self.update()

    def set_debug_config(self, d:dict):
        if not isinstance(d, dict): return
        if "show_centers" in d: self.debug_opts["show_centers"]=_parse_bool(d["show_centers"])
        if "sample_digit" in d:
            try: self.debug_opts["sample_digit"]=max(0, min(9, int(d["sample_digit"])))
            except Exception: pass
        self.update()

    # ---------- assets ----------
    def _pix(self, name:str):
        if not self.skin: return None
        a=self.skin.assets.get(name) or next((asset for key,asset in self.skin.assets.items() if key.lower()==name.lower()), None)
        if a:
            path, scale = a.path, a.scale
        else:
            root = Path(self.skin.root).resolve()
            path = (root / (name.replace("\\", "/") + ".png")).resolve()
            try: path.relative_to(root)
            except ValueError: return None
            hd = path.with_name(path.stem + "@2x.png")
            path, scale = (hd, 2) if hd.is_file() else (path, 1)
        # QPixmap(filename) caches by metadata and may return stale pixels after
        # an image is replaced with one of the same size/timestamp.
        pm=QPixmap()
        try: pm.loadFromData(Path(path).read_bytes())
        except OSError: return None
        if pm.isNull(): return None
        pm.setDevicePixelRatio(1.0)
        if scale==2: pm=pm.scaled(max(1, pm.width()//2), max(1, pm.height()//2), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        return pm

    def _tint(self, pm:QPixmap, color):
        if pm is None: return None
        img=pm.toImage().convertToFormat(QImage.Format_ARGB32)
        img.setDevicePixelRatio(1.0)
        out=QImage(img.size(), QImage.Format_ARGB32); out.fill(Qt.transparent)
        r,g,b=color; p=QPainter(out); p.setRenderHint(QPainter.Antialiasing, True)
        # Multiplication preserves painted shading; SourceIn flattened every pixel
        # into one solid combo colour, making textured hitcircles look incorrect.
        p.drawImage(0,0,img.convertToFormat(QImage.Format_RGB32))
        p.setCompositionMode(QPainter.CompositionMode_Multiply)
        p.fillRect(out.rect(), QColor(r,g,b))
        p.setCompositionMode(QPainter.CompositionMode_DestinationIn)
        p.drawImage(0,0,img)
        p.end()
        return QPixmap.fromImage(out)

    def _load_assets(self):
        # Artwork is aligned by image bounds in osu!, including its transparent
        # padding. Alpha recentering each layer independently distorts the skin.
        self.pm_circle=self._pix("hitcircle"); self.off_circle=(0,0)
        self.pm_overlay=self._pix("hitcircleoverlay"); self.off_overlay=(0,0)
        self.pm_approach=self._pix("approachcircle"); self.off_approach=_alpha_center(self.pm_approach)
        combo=None; overlay_rule=None; digit_prefix="default"; general={}
        if self.skin and self.skin.ini:
            for sec in self.skin.ini.sections():
                low=sec.lower()
                if low in ("colours","colors") and combo is None:
                    try: combo=next((v for k,v in self.skin.ini.items(sec) if k.lower()=="combo1"), None)
                    except Exception: pass
                if low in ("general","generalsettings") and overlay_rule is None:
                    general={k.lower():v for k,v in self.skin.ini.items(sec)}
                    try: overlay_rule=next((v for k,v in self.skin.ini.items(sec) if k.lower()=="hitcircleoverlayabovenumber"), None)
                    except Exception: pass
                if low=="fonts":
                    digit_prefix=next((v for k,v in self.skin.ini.items(sec) if k.lower()=="hitcircleprefix"), "default") or "default"
        for i in range(10):
            pm=self._pix(f"{digit_prefix}-{i}")
            self.pm_digits[i]=pm; self.off_digits[i]=(0,0)
        self.combo_color=_parse_rgb(combo, (0, 255, 255)); self.overlay_above_number=_parse_bool(overlay_rule, True)
        self.pm_circle_tinted=self._tint(self.pm_circle, self.combo_color)
        self.pm_approach_tinted=self._tint(self.pm_approach, self.combo_color)
        self.pm_cursor=self._pix("cursor")
        self.cursor_center=_parse_bool(general.get("cursorcentre"), True)
        self.cursor_rotate=_parse_bool(general.get("cursorrotate"), True)
        self.setCursor(Qt.BlankCursor if self.pm_cursor else Qt.ArrowCursor)

    def set_skin(self, skin):
        self.skin=skin; self.t=0; self._load_assets(); self.update()

    # ---------- draw ----------
    def tick(self):
        self.t=(self.t+16)%1600; self.update()

    def _draw_centered(self, painter:QPainter, cx:int, cy:int, pm:QPixmap, off, extra=(0,0)):
        if not pm: return
        x=cx-pm.width()//2+off[0]+(extra[0] if extra else 0)
        y=cy-pm.height()//2+off[1]+(extra[1] if extra else 0)
        painter.drawPixmap(int(x), int(y), pm)

    def _draw_cross(self, painter:QPainter, x:int, y:int, name:str=""):
        s=8
        painter.setPen(QPen(QColor(255,255,0,200),1))
        painter.drawLine(x-s,y,x+s,y); painter.drawLine(x,y-s,x,y+s)
        if name:
            painter.setPen(QPen(QColor(255,255,255,200),1))
            painter.drawText(x+6, y-6, name)

    def paintEvent(self, e):
        p=QPainter(self); p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)

        # Quiet slate canvas keeps transparent skin edges readable.
        bg=QLinearGradient(0, 0, self.width(), self.height())
        bg.setColorAt(0, QColor("#101d30")); bg.setColorAt(1, QColor("#08111e"))
        p.fillRect(self.rect(), bg)
        p.setPen(QPen(QColor(103,119,147,24),1))
        for x in range(24,self.width(),32): p.drawLine(x,0,x,self.height())
        for y in range(24,self.height(),32): p.drawLine(0,y,self.width(),y)

        if not self.skin:
            cx, cy = self.width()/2, self.height()/2-26
            p.setPen(QPen(QColor("#343e52"), 2)); p.setBrush(Qt.NoBrush)
            p.drawEllipse(QRectF(cx-77,cy-77,154,154))
            p.setPen(QPen(QColor("#77def4"), 5))
            p.drawEllipse(QRectF(cx-51,cy-51,102,102))
            p.setPen(QColor("#c7d1e3")); p.setFont(QFont("Segoe UI", 20, QFont.Bold))
            p.drawText(QRectF(cx-50,cy-27,100,54), Qt.AlignCenter, "osu!")
            p.setFont(QFont("Segoe UI", 11)); p.setPen(QColor("#94a3bd"))
            p.drawText(QRectF(20,cy+102,self.width()-40,42), Qt.AlignCenter,
                       i18n.t("preview.open_hint", "打开皮肤文件夹或导入 .osk，开始预览"))
            return

        cx,cy=self.width()//2,self.height()//2
        p.translate(cx,cy)
        p.scale(self.preview_scale,self.preview_scale)
        p.translate(-cx,-cy)
        base=self.pm_circle_tinted or self.pm_circle

        # approach
        if self.pm_approach:
            phase=(self.t%1600)/1600.0; scale=3.0-2.0*phase
            # A blank 1px hitcircle is common: its width must not shrink the
            # approachcircle (normally 126px) or overlay into invisibility.
            target_w=max(1,int(self.pm_approach.width()*scale))
            pm=self.pm_approach_tinted.scaledToWidth(target_w, Qt.SmoothTransformation)
            if self.approach_center_mode=="alpha":
                sx=pm.width()/self.pm_approach.width(); sy=pm.height()/self.pm_approach.height()
                off=(int(round(self.off_approach[0]*sx)), int(round(self.off_approach[1]*sy)))
            else:
                off=(0,0)
            p.setOpacity(0.9)
            self._draw_centered(p, cx, cy, pm, off, (self.user_offsets["approach_dx"], self.user_offsets["approach_dy"]))
            p.setOpacity(1.0)
            if self.debug_opts["show_centers"]:
                self._draw_cross(p, cx+self.user_offsets["approach_dx"], cy+self.user_offsets["approach_dy"], "approach")

        # hitcircle (base)
        hit_extra=(self.user_offsets["hit_dx"], self.user_offsets["hit_dy"])
        self._draw_centered(p, cx, cy, base, self.off_circle, hit_extra)
        if not base:
            p.setPen(QColor("#94a3bd"))
            p.drawText(self.rect().adjusted(20,20,-20,-20), Qt.AlignCenter,
                       i18n.t("preview.no_hitcircle", "此皮肤没有可预览的 hitcircle 图片"))
        if self.debug_opts["show_centers"]:
            self._draw_cross(p, cx+hit_extra[0], cy+hit_extra[1], "circle")

        # overlay (relative to circle)
        ovl_extra=(self.user_offsets["hit_dx"]+self.user_offsets["ovl_dx"],
                   self.user_offsets["hit_dy"]+self.user_offsets["ovl_dy"])

        # number
        d=self.debug_opts.get("sample_digit",6); d=max(0,min(9,int(d)))
        digit=self.pm_digits[d]; digit_off=self.off_digits[d] if len(self.off_digits)>d else (0,0)

        if self.user_offsets.get("link_num",1):
            num_extra=(self.user_offsets["hit_dx"]+self.user_offsets["num_dx"],
                       self.user_offsets["hit_dy"]+self.user_offsets["num_dy"])
        else:
            num_extra=(self.user_offsets["num_dx"], self.user_offsets["num_dy"])

        if self.overlay_above_number:
            if digit: self._draw_centered(p, cx, cy, digit, digit_off, num_extra)
            self._draw_centered(p, cx, cy, self.pm_overlay, self.off_overlay, ovl_extra)
        else:
            self._draw_centered(p, cx, cy, self.pm_overlay, self.off_overlay, ovl_extra)
            if digit: self._draw_centered(p, cx, cy, digit, digit_off, num_extra)

        if self.debug_opts["show_centers"]:
            self._draw_cross(p, cx+ovl_extra[0], cy+ovl_extra[1], "overlay")
            self._draw_cross(p, cx+num_extra[0], cy+num_extra[1], "number")

        if self.pm_cursor:
            if self._mouse_position is not None:
                cursor_x=cx+(self._mouse_position.x()-cx)/self.preview_scale
                cursor_y=cy+(self._mouse_position.y()-cy)/self.preview_scale
            else:
                angle=self.t/1600.0*math.tau
                radius=max(88, (base.width()/2+36) if base else 88)
                cursor_x=cx+math.cos(angle)*radius
                cursor_y=cy+math.sin(angle)*radius
            p.save()
            p.translate(cursor_x,cursor_y)
            if self.cursor_rotate: p.rotate(self.t/1600.0*360)
            x=-self.pm_cursor.width()/2 if self.cursor_center else 0
            y=-self.pm_cursor.height()/2 if self.cursor_center else 0
            p.drawPixmap(int(x),int(y),self.pm_cursor)
            p.restore()
