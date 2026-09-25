# osu! 小蓝皮肤编辑器

基于 PySide6 的 Windows 皮肤编辑与设计工具，提供 Standard / Mania 演示预览、素材管理、Mania 配置编辑与可导出的视觉设计

## 常用功能

- 深蓝与冰青界面、小蓝头像，右上角独立「语言 / Language」入口
- 打开或拖入包含 skin.ini 的皮肤文件夹，支持最近记录、搜索、分类和透明图片预览
- Standard 图层预览、缩放、暂停和皮肤光标
- Mania 键数、1–40 流速、演示 BPM、判定参考线、暂停与重置
- 图片替换、音频试听与替换、同名音频冲突整理
- Mania 配置读写、备份、快照与恢复
- 上隐遮罩、渐变和颜色、底部加高及判定位置联动，保存为新皮肤副本

## 运行

安装 Python 3.10 或更新版本，在项目目录运行

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe app.py
```

点击「打开皮肤」选择直接包含 skin.ini 的目录，或将目录 / skin.ini 拖入窗口

快捷键：Ctrl+O 打开、F5 刷新；Mania 配置面板内 Ctrl+S 保存、Ctrl+R 重新读取

数字框和键数下拉框需要先点击，才能用滚轮修改；失焦后重新锁定，普通滚轮继续滚动配置面板

## Mania 视觉设计

1. 切换到 osu!mania，选择要编辑的键数
2. 调整流速和演示节奏，打开判定参考线检查图片与逻辑判定位置
3. 点击「皮肤设计」，设置上隐的遮挡高度、渐变和颜色
4. 用「底部加高」增加按键图片底部透明留白，使球槽上移；默认联动 HitPosition，取消勾选后只移动图像
5. 点击「保存皮肤副本」，确认新文件夹名称与位置
6. 保存成功后打开副本，设计参数归零，已保存效果作为新的基准；从最近记录打开原皮肤即可重新基于原图设计

流速、BPM 和判定参考线仅用于预览，不写入皮肤配置；演示采用固定速度，不模拟谱面 SV、音乐同步或游戏 Mod

保存时复制原皮肤，生成 StageBottom 与 KeyImage 衍生图片，仅更新选中键数的配置；原目录、其他键数、共享原图和旧副本不被覆盖

上隐保存支持普通下落方向、单舞台的 1–9K；底部加高要求皮肤内存在实际的松开和按下键图，无法编辑游戏内置的回退图片

导出保留原有 StageBottom 图案与全部动画帧，预览当前显示第一帧

## 素材写入与备份

图片和音频替换会立即写入当前皮肤目录，替换前保留原文件备份；关闭管理窗口后主预览刷新

Mania 配置在点击保存后写入，未保存配置与视觉设计在离开时分别提示保存、放弃或取消

| 位置 | 用途 |
| --- | --- |
| skin.ini.bak | 首次保存前的原始配置，后续保存不覆盖 |
| .skin_ini_history/ | 配置历史快照，可在 Mania 面板恢复 |
| __conflicts_backup/ | 素材替换和音频冲突整理前的原文件 |
| editor-assets/mania-*/ | 设计副本中生成的独立素材 |

恢复素材时从备份复制回原位置，再按 F5；更换过音频格式时，检查同名不同后缀的文件

WAV / OGG / MP3 替换保留源格式，FLAC 转换依赖 PATH 中的 FFmpeg；FFmpeg 不随程序附带

## 预览范围

Standard 展示圆圈、覆盖层、数字、缩圈及光标；Mania 展示轨道、按键、音符、判定位置、StageHint 与 StageBottom

按键图片按舞台底部定位，保留透明留白；音符以底边定位，判定图像与实际判定位置分别处理

当前不读取完整谱面，也不完整模拟滑条、转盘、长按音符、游戏判定或全部皮肤配置，最终效果需在 osu! 内查看

参考：[skin.ini](https://osu.ppy.sh/wiki/en/Skinning/skin.ini)、[Mania 素材说明](https://osu.ppy.sh/wiki/en/Skinning/osu%21mania)、[按键渲染](https://github.com/ppy/osu/blob/master/osu.Game.Rulesets.Mania/Skinning/Legacy/LegacyKeyArea.cs)、[StageBottom 渲染](https://github.com/ppy/osu/blob/master/osu.Game.Rulesets.Mania/Skinning/Legacy/LegacyStageForeground.cs)

## 开发与打包

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m pip install pyinstaller
.\.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean OsuSkinEditor.spec
```

生成文件为 dist/OsuSkinEditor.exe，requirements-lock.txt 记录当前构建依赖

137 项回归测试通过，覆盖文件保护、导出失败、编码、其他键数、真实像素对照、播放时间及界面交互；实际设计导出验证覆盖 Capoo 1.5 与 Bochi 圆球 v3.0

core/osk_io.py 提供 OSK 导入导出 API，目前尚未接入主界面

[更新记录](CHANGELOG.md) · [程序头像和背景编辑提示词](assets/branding/README.md)
