"""Deterministic synthetic osu! scene, rendered in a shared 480-high viewport.

No beatmaps, score files, or skin assets are modified. Slider bodies are vector
approximations; native skin artwork retains its padding and resolution density.
"""
from dataclasses import dataclass
import math

from PySide6.QtCore import Qt, QPointF, QRectF
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen


@dataclass(frozen=True)
class SceneObject:
    kind: str
    start: float
    x: float
    y: float
    duration: float = 0
    end_x: float = 0
    end_y: float = 0
    number: int = 1
    combo: int = 0
    result: str = "300"

    @property
    def end(self):
        return self.start+self.duration


def make_scene(pattern):
    if pattern == "spinner":
        return (SceneObject("spinner", 1200, 256, 192, 4200),), 6800
    if pattern == "sliders":
        return tuple(SceneObject("slider", 1200+i*2900, x, y, 2200, ex, ey, i+1, i)
                     for i, (x, y, ex, ey) in enumerate(((100, 110, 380, 230), (390, 100, 120, 270), (120, 260, 360, 100)))), 10000
    points = ((110, 110), (270, 95), (395, 200), (285, 280), (125, 260), (90, 165), (265, 190), (400, 100))
    if pattern == "circles":
        results = ("300", "300", "100", "300", "50", "300", "0", "300")
        return tuple(SceneObject("circle", 1000+i*520, x, y, number=i%4+1, combo=i//4, result=results[i])
                     for i, (x, y) in enumerate(points)), 6000
    return (
        SceneObject("circle", 1000, 110, 120, number=1, combo=0),
        SceneObject("circle", 1500, 255, 95, number=2, combo=0),
        SceneObject("circle", 2000, 385, 175, number=3, combo=0, result="100"),
        SceneObject("circle", 2500, 280, 285, number=4, combo=0),
        SceneObject("slider", 3300, 100, 240, 2000, 380, 125, 1, 1),
        SceneObject("circle", 5800, 400, 260, number=2, combo=1, result="50"),
        SceneObject("circle", 6300, 235, 190, number=3, combo=1),
        SceneObject("circle", 6800, 105, 100, number=4, combo=1, result="0"),
        SceneObject("slider", 7700, 135, 280, 2200, 395, 140, 1, 2),
        SceneObject("spinner", 11000, 256, 192, 3300, number=1, combo=3),
    ), 15500


class StdScene:
    def __init__(self, assets):
        self.assets = assets
        self.pattern = "mixed"
        self.objects, self.period = make_scene(self.pattern)

    def set_pattern(self, pattern):
        self.pattern = pattern
        self.objects, self.period = make_scene(pattern)

    def viewport_bounds(self, width):
        bounds = QRectF(0, 0, width, 480)
        for sprite in self.assets.combo_bursts:
            visible = self.assets.visible_bounds(sprite)
            _, height = sprite.logical_size()
            if not visible.isEmpty():
                bounds = bounds.united(QRectF(visible.left()*.625, 480-height*.625+visible.top()*.625,
                                              visible.width()*.625, visible.height()*.625))
        sprite = self.assets.images.get("scorebar-bg")
        visible = self.assets.visible_bounds(sprite)
        if not visible.isEmpty():
            bounds = bounds.united(QRectF(visible.left()*.625, visible.top()*.625,
                                          visible.width()*.625, visible.height()*.625))
        fill = self.assets.images.get("scorebar-colour")
        if fill:
            marker = self.assets.images.get("scorebar-marker")
            x, y = (7.5, 7.8) if marker else (3, 10)
            fill_w, fill_h = fill.logical_size()
            visible = self.assets.visible_bounds(fill).intersected(QRectF(0, 0, fill_w*.8, min(120, fill_h)))
            if not visible.isEmpty():
                bounds = bounds.united(QRectF(x+visible.left()*.625, y+visible.top()*.625,
                                              visible.width()*.625, visible.height()*.625))
            tip = marker or self.assets.images.get("scorebar-ki")
            if tip:
                visible = self.assets.visible_bounds(tip)
                tip_w, tip_h = tip.logical_size()
                if not visible.isEmpty():
                    bounds = bounds.united(QRectF(x+fill_w*.625*.8+(visible.left()-tip_w/2)*.625,
                                                  y+(min(120, fill_h)*.625/2 if marker else 0)+(visible.top()-tip_h/2)*.625,
                                                  visible.width()*.625, visible.height()*.625))
        return bounds

    def visible(self, time_ms, preempt):
        cycle = int(time_ms//self.period)
        return [(obj, obj.start+turn*self.period) for turn in (cycle-1, cycle, cycle+1) if turn >= 0
                for obj in self.objects if obj.start+turn*self.period-preempt <= time_ms <= obj.end+turn*self.period+550]

    def stats(self, time_ms):
        cycle = int(time_ms//self.period)
        phase = time_ms%self.period
        completed = [obj for obj in self.objects if obj.end <= phase]
        grades = [obj.result for obj in self.objects]
        score_values = {"300": 30000, "100": 10000, "50": 5000, "0": 0}
        score = cycle*sum(score_values[g] for g in grades)+sum(score_values[o.result] for o in completed)
        count = cycle*len(self.objects)+len(completed)
        combo = count
        if "0" in grades:
            recent = (grades if cycle else [])+[obj.result for obj in completed]
            combo = next((i for i, value in enumerate(reversed(recent)) if value == "0"), len(recent))
        return score, combo, count

    @staticmethod
    def sprite_rect(sprite, x, y, scale=1, time_ms=0, centered=True):
        width, height = sprite.logical_size(time_ms)
        return QRectF(x-width*scale/2 if centered else x, y-height*scale/2 if centered else y,
                      width*scale, height*scale)

    def sprite(self, p, sprite, x, y, scale=1, time_ms=0, tint=None, rotation=0, opacity=1, centered=True):
        if sprite is None:
            return False
        p.save()
        p.setOpacity(p.opacity()*opacity)
        p.translate(x, y)
        p.rotate(rotation)
        rect = self.sprite_rect(sprite, 0, 0, scale, time_ms, centered)
        image = self.assets.tinted(sprite.frame_at(time_ms), tint)
        p.drawPixmap(rect, image, QRectF(image.rect()))
        p.restore()
        return True

    def number(self, p, value, font, x, y, scale, time_ms=0, align="center", fixed_width=False):
        text = str(value)
        sprites = [self.assets.fonts[font].get(ch) for ch in text]
        sizes = [sp.logical_size(time_ms) if sp else (22, 34) for sp in sprites]
        five = self.assets.fonts[font].get("5")
        cell_width = five.logical_size(time_ms)[0] if five else 22
        advances = [cell_width if fixed_width and ch.isdigit() else size[0] for ch, size in zip(text, sizes)]
        overlap = self.assets.overlaps[font]*scale
        width = sum(advance*scale for advance in advances)-overlap*(len(sprites)-1)
        left = x-width/2 if align == "center" else x-width if align == "right" else x
        for char, sprite, size, advance in zip(text, sprites, sizes, advances):
            if sprite:
                self.sprite(p, sprite, left+advance*scale/2, y, scale, time_ms)
            else:
                p.setFont(QFont("Segoe UI", max(5, int(28*scale)), QFont.Bold))
                p.setPen(QColor("#e9f7ff"))
                p.drawText(QRectF(left, y-20*scale, advance*scale, 40*scale), Qt.AlignCenter, char)
            left += advance*scale-overlap

    def _circle(self, p, obj, x, y, time_ms, start, circle_scale, preempt, offsets, slider_part=None):
        age = time_ms-start
        tint = self.assets.combo_colours[obj.combo%len(self.assets.combo_colours)]
        fade = max(0, min(1, (age+preempt)/min(400, preempt)))
        expand = 1
        if age > 0:
            fade *= max(0, 1-age/250)
            expand = 1+.4*min(1, age/250)
        if fade <= 0:
            return
        p.save()
        p.setOpacity(p.opacity()*fade)
        art_scale = circle_scale*expand
        hx, hy = x+offsets["hit_dx"]*circle_scale, y+offsets["hit_dy"]*circle_scale
        base_name = slider_part if slider_part and self.assets.images.get(slider_part) is not None else "hitcircle"
        base = self.assets.images.get(base_name)
        overlay = self.assets.images.get(base_name+"overlay")
        if age < 0 and slider_part != "sliderendcircle":
            approach_scale = 1+2*min(1, -age/preempt)
            self.sprite(p, self.assets.images["approachcircle"],
                        x+offsets["approach_dx"]*circle_scale, y+offsets["approach_dy"]*circle_scale,
                        circle_scale*approach_scale, time_ms, tint)
        if not self.sprite(p, base, hx, hy, art_scale, time_ms, tint):
            p.setPen(QPen(tint.lighter(150), 2))
            p.setBrush(tint.darker(190))
            radius = 59*art_scale
            p.drawEllipse(QPointF(hx, hy), radius, radius)
        ox, oy = hx+offsets["ovl_dx"]*circle_scale, hy+offsets["ovl_dy"]*circle_scale
        if not self.assets.overlay_above:
            self.sprite(p, overlay, ox, oy, art_scale, time_ms)
        if slider_part != "sliderendcircle":
            nx = (hx if offsets["link_num"] else x)+offsets["num_dx"]*circle_scale
            ny = (hy if offsets["link_num"] else y)+offsets["num_dy"]*circle_scale
            number_scale = circle_scale*.8*(expand if self.assets.version < 2 else 1)
            self.number(p, obj.number, "hitcircle", nx, ny, number_scale, time_ms)
        if self.assets.overlay_above:
            self.sprite(p, overlay, ox, oy, art_scale, time_ms)
        p.restore()

    @staticmethod
    def slider_path(obj, origin):
        start = QPointF(origin.x()+obj.x, origin.y()+obj.y)
        end = QPointF(origin.x()+obj.end_x, origin.y()+obj.end_y)
        control = QPointF((start.x()+end.x())/2, min(start.y(), end.y())-60)
        path = QPainterPath(start)
        path.quadTo(control, end)
        return path

    def _slider(self, p, obj, start, time_ms, origin, cs, preempt, offsets):
        path = self.slider_path(obj, origin)
        elapsed = time_ms-start
        tint = self.assets.combo_colours[obj.combo%len(self.assets.combo_colours)]
        fade = min(1, max(0, (elapsed+preempt)/min(400, preempt)), max(0, (obj.duration+250-elapsed)/250))
        p.save()
        p.setOpacity(p.opacity()*fade)
        # Stable's body is procedural, not a stretched PNG. These rounded
        # strokes approximate its border and track without distorting artwork.
        p.setPen(QPen(self.assets.slider_border, 118*cs, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.drawPath(path)
        track = self.assets.slider_track or tint.darker(290)
        p.setPen(QPen(track, 108*cs, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.drawPath(path)
        progress = max(0, min(1, elapsed/obj.duration))
        route = 1-abs(1-progress*2)
        for fraction in (.2, .4, .6, .8):
            if elapsed < 0 or progress < .5 and fraction > route or progress >= .5 and fraction < route:
                point = path.pointAtPercent(fraction)
                if not self.sprite(p, self.assets.images["sliderscorepoint"], point.x(), point.y(), cs, time_ms):
                    p.setPen(Qt.NoPen)
                    p.setBrush(QColor("white"))
                    p.drawEllipse(point, 3*cs, 3*cs)
        tail = path.pointAtPercent(1 if progress < .5 else 0)
        angle = -path.angleAtPercent(1 if progress < .5 else 0)+(180 if progress < .5 else 0)
        # Both slider endpoints use the skin's independent circle/overlay set.
        end_obj = SceneObject("circle", 0, 0, 0, number=obj.number, combo=obj.combo)
        point = path.pointAtPercent(1)
        self._circle(p, end_obj, point.x(), point.y(), min(time_ms, start-1), start, cs, preempt, offsets, "sliderendcircle")
        if elapsed < obj.duration-100:
            self.sprite(p, self.assets.images["reversearrow"], tail.x(), tail.y(), cs*(1.1+.1*math.sin(time_ms/100)), time_ms, rotation=angle)
        point = path.pointAtPercent(0)
        if elapsed < 250:
            self._circle(p, obj, point.x(), point.y(), time_ms, start, cs, preempt, offsets, "sliderstartcircle")
        if 0 <= elapsed <= obj.duration:
            ball = path.pointAtPercent(route)
            self.sprite(p, self.assets.images["sliderfollowcircle"], ball.x(), ball.y(), cs, time_ms)
            rotation = -path.angleAtPercent(route)+(180 if progress >= .5 and self.assets.slider_ball_flip else 0)
            self.sprite(p, self.assets.images["sliderb-nd"], ball.x(), ball.y(), cs, time_ms)
            ball_tint = tint if self.assets.slider_ball_tint else self.assets.slider_ball_colour
            if not self.sprite(p, self.assets.images["sliderb"], ball.x(), ball.y(), cs, time_ms, ball_tint, rotation):
                p.setPen(QPen(QColor("white"), 2))
                p.setBrush(ball_tint)
                p.drawEllipse(ball, 44*cs, 44*cs)
            self.sprite(p, self.assets.images["sliderb-spec"], ball.x(), ball.y(), cs, time_ms)
        p.restore()

    def _spinner(self, p, obj, start, time_ms, width):
        elapsed = time_ms-start
        if elapsed < 0:
            opacity = max(0, min(1, (elapsed+400)/400))
        else:
            opacity = max(0, min(1, (obj.duration+300-elapsed)/300))
        if opacity <= 0:
            return
        p.save()
        p.setOpacity(p.opacity()*opacity)
        cx, cy = width/2, 240
        progress = max(0, min(1, elapsed/obj.duration))
        rotation = max(0, elapsed)*.55
        new_style = self.assets.version >= 2
        names = ("spinner-glow", "spinner-bottom", "spinner-top", "spinner-middle2", "spinner-middle") if new_style else ("spinner-background", "spinner-circle")
        found = False
        for name in names:
            angle = rotation/6 if name == "spinner-bottom" else rotation/2 if name == "spinner-top" else rotation if name in ("spinner-middle2", "spinner-circle") else 0
            tint = self.assets.spinner_background if name == "spinner-background" else None
            found |= self.sprite(p, self.assets.images[name], cx, cy, .625, time_ms, tint, angle,
                                 progress if name == "spinner-glow" else 1)
        if not found:
            p.setPen(QPen(QColor("#66ddff"), 8))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(QPointF(cx, cy), 120, 120)
            p.drawLine(QPointF(cx, cy), QPointF(cx+math.cos(math.radians(rotation))*110, cy+math.sin(math.radians(rotation))*110))
        self.sprite(p, self.assets.images["spinner-approachcircle"], cx, cy, .625*(1.86-1.76*progress), time_ms)
        if not new_style and self.assets.images["spinner-metre"]:
            p.save()
            p.setClipRect(QRectF(cx-230, 390-330*progress, 100, 330*progress), Qt.IntersectClip)
            self.sprite(p, self.assets.images["spinner-metre"], cx-230, 60, .625, time_ms, centered=False)
            p.restore()
        self.sprite(p, self.assets.images["spinner-clear" if progress > .85 else "spinner-spin"], cx, 140 if progress > .85 else 365, .625, time_ms)
        self.sprite(p, self.assets.images["spinner-rpm"], cx-100, 436, .625, time_ms, centered=False)
        self.number(p, 330+int(30*math.sin(time_ms/100)), "score", cx+70, 450, .45, time_ms, "right")
        p.restore()

    def cursor_position(self, time_ms, origin):
        cycle = int(time_ms//self.period)
        local = time_ms-cycle*self.period
        active = next((o for o in self.objects if o.kind in ("slider", "spinner") and o.start <= local <= o.end), None)
        if active and active.kind == "slider":
            progress = (local-active.start)/active.duration
            return self.slider_path(active, origin).pointAtPercent(1-abs(1-2*progress))
        if active:
            angle = (local-active.start)/180
            return QPointF(origin.x()+256+math.cos(angle)*100, 240+math.sin(angle)*100)
        previous = next((o for o in reversed(self.objects) if o.end <= local), None)
        upcoming = next((o for o in self.objects if o.start > local), self.objects[0])
        start = QPointF(origin.x()+(previous.x if previous else 256), origin.y()+(previous.y if previous else 192))
        end = QPointF(origin.x()+upcoming.x, origin.y()+upcoming.y)
        previous_time = previous.end if previous else 0
        duration = max(1, upcoming.start-previous_time)
        progress = max(0, min(1, (local-previous_time)/duration))
        progress = progress*progress*(3-2*progress)
        return start+(end-start)*progress

    def judgement(self, p, kind, x, y, age, manual=False):
        if not manual and not 0 <= age < 550:
            return
        opacity = 1 if manual else max(0, min(1, (550-age)/220))
        scale = .625 if manual else .625*(1+.3*max(0, 1-age/180))
        if not self.sprite(p, self.assets.images.get("hit"+kind), x, y, scale, age, opacity=opacity):
            p.save()
            p.setOpacity(opacity)
            p.setPen(QColor("#ff7799" if kind == "0" else "#b7f6ff"))
            p.setFont(QFont("Segoe UI", 18, QFont.Bold))
            p.drawText(QRectF(x-60, y-20, 120, 40), Qt.AlignCenter, "MISS" if kind == "0" else kind)
            p.restore()

    def health_bar(self, p, time_ms):
        """Fixed 80% sample health, using legacy image offsets and cropping."""
        images = self.assets.images
        self.sprite(p, images["scorebar-bg"], 0, 0, .625, time_ms, centered=False)
        fill = images["scorebar-colour"]
        if fill is None:
            return
        marker = images["scorebar-marker"]
        x, y = (7.5, 7.8) if marker else (3, 10)
        logical_w, logical_h = fill.logical_size(0)
        # The health amount clips the image horizontally; it never stretches
        # the painted texture into a smaller bar. Wiki caps fill height at120SD.
        height = min(120, logical_h)*.625
        fill_width = logical_w*.625*.8
        p.save()
        p.setClipRect(QRectF(x, y, fill_width, height), Qt.IntersectClip)
        self.sprite(p, fill, x, y, .625, time_ms, centered=False)
        p.restore()
        p.save()
        if marker:
            p.setCompositionMode(QPainter.CompositionMode_Plus)
        self.sprite(p, marker or images["scorebar-ki"], x+fill_width, y+height/2 if marker else y, .625, time_ms)
        p.restore()

    def paint(self, p, time_ms, width, cs, preempt, offsets, manual_judgement=None, manual_burst=None, pointer=None):
        origin = QPointF((width-512)/2, 48)
        circle_scale = (1-.7*((cs-5)/5))/2
        visible = self.visible(time_ms, preempt)
        followpoint = self.assets.images["followpoint"]
        if followpoint:
            for (previous, previous_start), (upcoming, next_start) in zip(visible, visible[1:]):
                if previous.kind != "circle" or upcoming.kind != "circle" or previous.combo != upcoming.combo:
                    continue
                start = QPointF(origin.x()+previous.x, origin.y()+previous.y)
                end = QPointF(origin.x()+upcoming.x, origin.y()+upcoming.y)
                delta = end-start
                distance = math.hypot(delta.x(), delta.y())
                angle = math.degrees(math.atan2(delta.y(), delta.x()))
                count = max(1, int(distance/26))
                for index in range(1, count):
                    fraction = index/count
                    if fraction*distance < 68*circle_scale or (1-fraction)*distance < 68*circle_scale:
                        continue
                    point = start+delta*fraction
                    self.sprite(p, followpoint, point.x(), point.y(), .625, time_ms, rotation=angle, opacity=.6)
        # Earlier notes appear above later notes, while slider tracks stay below.
        for obj, start in reversed(visible):
            if obj.kind == "slider":
                self._slider(p, obj, start, time_ms, origin, circle_scale, preempt, offsets)
        for obj, start in reversed(visible):
            if obj.kind == "circle":
                self._circle(p, obj, origin.x()+obj.x, origin.y()+obj.y, time_ms, start, circle_scale, preempt, offsets)
            elif obj.kind == "spinner":
                self._spinner(p, obj, start, time_ms, width)
        for obj, start in visible:
            age = time_ms-start-obj.duration
            if age >= 0:
                self.judgement(p, obj.result, origin.x()+obj.x, origin.y()+obj.y, age)
        score, combo, count = self.stats(time_ms)
        self.health_bar(p, time_ms)
        five = self.assets.fonts["score"].get("5")
        font_height = five.logical_size(time_ms)[1] if five else 34
        score_bottom = font_height*.6
        self.number(p, f"{score:08d}", "score", width-6.25, score_bottom/2, .6, time_ms, "right", True)
        accuracy = 100 if count == 0 else (score*10000//(count*30000))/100
        self.number(p, f"{accuracy:.2f}%", "score", width-10.625, score_bottom+5.625+font_height*.36/2,
                    .36, time_ms, "right", True)
        self.number(p, str(combo)+"x", "combo", 16, 451, .8, time_ms, "left")
        automatic_burst = False
        if manual_burst is None and count and count%10 == 0:
            cycle, index = divmod(count-1, len(self.objects))
            age = time_ms-(cycle*self.period+self.objects[index].end)
            if 0 <= age < 1200:
                manual_burst = (count//10-1, age)
                automatic_burst = True
        if manual_burst is not None and self.assets.combo_bursts:
            index, age = manual_burst
            sprite = self.assets.combo_bursts[index%len(self.assets.combo_bursts)]
            sw, sh = sprite.logical_size(age)
            scale = .625
            self.sprite(p, sprite, sw*scale/2, 480-sh*scale/2, scale, age,
                        opacity=min(1, (1200-age)/250) if automatic_burst else 1)
        if manual_judgement:
            kind, age = manual_judgement
            self.judgement(p, kind, width/2, 225, age, True)
        position = pointer or self.cursor_position(time_ms, origin)
        for index in range(10, 0, -1):
            point = self.cursor_position(max(0, time_ms-index*12), origin) if pointer is None else position
            self.sprite(p, self.assets.images["cursortrail"], point.x(), point.y(), .625, time_ms,
                        rotation=time_ms/8 if self.assets.cursor_trail_rotate else 0, opacity=(1-index/11)*.5)
        cursor_scale = .625*(1+.15*max(0, 1-(time_ms%500)/100)) if self.assets.cursor_expand else .625
        if not self.sprite(p, self.assets.images["cursor"], position.x(), position.y(), cursor_scale, time_ms,
                           rotation=time_ms/8 if self.assets.cursor_rotate else 0, centered=self.assets.cursor_center):
            p.setPen(QPen(QColor("#74e7ff"), 2))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(position, 7, 7)
        self.sprite(p, self.assets.images["cursormiddle"], position.x(), position.y(), .625, time_ms,
                    centered=self.assets.cursor_center)
