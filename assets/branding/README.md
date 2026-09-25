# 小蓝科技风头像

- `xiaolan-tech.png`：使用 Codex 内置 `image_gen` 编辑模式生成的科技风背景版本，用于程序品牌头像
- 原始头像备份 `xiaolan-original.png` 仅保存在本地并由 Git 忽略，运行与打包不依赖该备份
- 未使用 CLI/API 回退或 Python 图像编辑
- 视觉核对：保留蓝白狼角色、表情、紫色眼睛、衣服和粉色 osu! 圆盘；背景改为深海军蓝、青色电路线与柔光，生成式编辑不是像素级无损换背景

## 最终提示词

```text
Use case: precise-object-edit.
Asset type: square avatar artwork for the upper-left app badge of a dark navy and ice-cyan technology-styled osu! skin editor. Requested output: 1024x1024 pixels, square.
Input image: the attached local image is the EDIT TARGET, not merely a style reference.
Primary request: Replace ONLY the royal-blue dotted backdrop with a sophisticated dark navy backdrop, subtle sparse ice-cyan circuit traces and a soft luminous cyan halo behind the wolf's head. Keep the backdrop understated so the portrait is legible at small UI sizes.
Subject invariants: Preserve the exact blue-and-white wolf character identity, original hand-drawn line art, face, pose, expression, eye shape and purple eye color, fur markings and blue-white fur colors, clothing, hands, and held pink osu! disc. Preserve the original text and shapes on the held disc. Do not redraw, redesign, recolor, or add details to the character or disc.
Composition: Preserve original composition with only slight padding as needed for a square image. Keep the ears fully inside the image. No decorative border or frame crossing the character. Background work must stay behind the existing character.
Avoid: extra text, watermarks, extra objects, extra limbs, changes to facial expression, dramatic relighting of the subject, cropping the ears, or busy dense circuitry.
```
