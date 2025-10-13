# -*- coding: utf-8 -*-
from PySide6.QtWidgets import QWidget
from PySide6.QtGui import QPainter, QPen, QPixmap, QColor, QImage
from PySide6.QtCore import Qt, QTimer

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
    return s in ("1","true","yes","on")

def _alpha_center(pm: QPixmap, thresh: int = 10):
    if pm is None or pm.isNull(): return (0,0)
    img = pm.toImage().convertToFormat(QImage.Format_ARGB32)
    w, h = img.width(), img.height()
    minx, miny, maxx, maxy = w, h, -1, -1
    for y in range(h):
        for x in range(w):
            a = img.pixelColor(x, y).alpha()
            if a > thresh:
                if x < minx: minx = x
                if y < miny: miny = y
                if x > maxx: maxx = x
                if y > maxy: maxy = y
    if maxx < minx or maxy < miny: return (0,0)
    cx_img, cy_img = w/2.0, h/2.0; cx_cnt, cy_cnt = (minx+maxx)/2.0, (miny+maxy)/2.0
    return (int(round(cx_img - cx_cnt)), int(round(cy_img - cy_cnt)))

class StdPreview(QWidget):
    def __init__(self):
        super().__init__()
        self.skin = None
        self.t = 0
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(16)

        self.combo_color = (0, 255, 255)
        self.pm_circle=None; self.off_circle=(0,0)
        self.pm_overlay=None; self.off_overlay=(0,0)
        self.pm_approach=None; self.off_approach=(0,0)
        self.pm_digits=[None]*10; self.off_digits=[(0,0)]*10
        self.pm_circle_tinted=None; self.overlay_above_number=True

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
        if "show_centers" in d: self.debug_opts["show_centers"]=bool(d["show_centers"])
        if "sample_digit" in d:
            try: self.debug_opts["sample_digit"]=max(0, min(9, int(d["sample_digit"])))
            except Exception: pass
        self.update()

    # ---------- assets ----------
    def _pix(self, name:str):
        if not self.skin: return None
        a=self.skin.assets.get(name); 
        if not a: return None
        pm=QPixmap(str(a.path))
        if a.scale==2: pm=pm.scaled(pm.width()//2, pm.height()//2, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        return pm

    def _tint(self, pm:QPixmap, color):
        if pm is None: return None
        img=pm.toImage().convertToFormat(QImage.Format_ARGB32)
        out=QImage(img.size(), QImage.Format_ARGB32); out.fill(Qt.transparent)
        r,g,b=color; p=QPainter(out); p.setRenderHint(QPainter.Antialiasing, True)
        p.drawImage(0,0,img); p.setCompositionMode(QPainter.CompositionMode_SourceIn); p.fillRect(out.rect(), QColor(r,g,b)); p.end()
        return QPixmap.fromImage(out)

    def _load_assets(self):
        self.pm_circle=self._pix("hitcircle"); self.off_circle=_alpha_center(self.pm_circle)
        self.pm_overlay=self._pix("hitcircleoverlay"); self.off_overlay=_alpha_center(self.pm_overlay)
        self.pm_approach=self._pix("approachcircle"); self.off_approach=_alpha_center(self.pm_approach)
        for i in range(10):
            pm=self._pix(f"default-{i}") or self._pix(f"score-{i}")
            self.pm_digits[i]=pm; self.off_digits[i]=_alpha_center(pm) if pm else (0,0)

        combo=None; overlay_rule=None
        if self.skin and self.skin.ini:
            for sec in self.skin.ini.sections():
                low=sec.lower()
                if low in ("colours","colors") and combo is None:
                    try: combo=self.skin.ini.get(sec, "Combo1", fallback=None)
                    except Exception: pass
                if low in ("general","generalsettings") and overlay_rule is None:
                    try: overlay_rule=self.skin.ini.get(sec, "HitCircleOverlayAboveNumber", fallback=None)
                    except Exception: pass
        self.combo_color=_parse_rgb(combo, self.combo_color); self.overlay_above_number=_parse_bool(overlay_rule, True)
        self.pm_circle_tinted=self._tint(self.pm_circle, self.combo_color)

    def set_skin(self, skin):
        self.skin=skin; self._load_assets(); self.update()

    # ---------- draw ----------
    def tick(self):
        self.t=(self.t+16)%2000; self.update()

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
        p=QPainter(self); p.setRenderHint(QPainter.Antialiasing, True)

        # grid
        p.fillRect(self.rect(), Qt.black)
        p.setPen(QPen(QColor(200,200,200,120),1,Qt.DotLine))
        for x in range(0,self.width(),32): p.drawLine(x,0,x,self.height())
        for y in range(0,self.height(),32): p.drawLine(0,y,self.width(),y)

        cx,cy=self.width()//2,self.height()//2
        base=self.pm_circle_tinted or self.pm_circle

        # approach
        if base and self.pm_approach:
            phase=(self.t%1600)/1600.0; scale=1.6-0.6*phase
            target_w=max(1,int(base.width()*scale))
            pm=self.pm_approach.scaledToWidth(target_w, Qt.SmoothTransformation)
            if self.approach_center_mode=="alpha":
                sx=pm.width()/self.pm_approach.width(); sy=pm.height()/self.pm_approach.height()
                off=(int(round(self.off_approach[0]*sx)), int(round(self.off_approach[1]*sy)))
            else:
                off=(0,0)
            pm_tinted=self._tint(pm, self.combo_color)
            p.setOpacity(0.9)
            self._draw_centered(p, cx, cy, pm_tinted, off, (self.user_offsets["approach_dx"], self.user_offsets["approach_dy"]))
            p.setOpacity(1.0)
            if self.debug_opts["show_centers"]:
                self._draw_cross(p, cx+self.user_offsets["approach_dx"], cy+self.user_offsets["approach_dy"], "approach")

        # hitcircle (base)
        hit_extra=(self.user_offsets["hit_dx"], self.user_offsets["hit_dy"])
        self._draw_centered(p, cx, cy, base, self.off_circle, hit_extra)
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
