# osu! XiaoLan Skin Editor（含 STD + MANIA 预览）

- 打开皮肤目录（包含 `skin.ini`）
- 自动发现常见素材（优先使用 `@2x`，预览时按 0.5 缩放）
- STD 预览：网格 + `hitcircle`/`overlay` + 简单移动 `cursor`
- MANIA 预览：按 `Keys` 列数画轨道 + 判定线 + 下落 note（演示）

## 安装
```bash
pip install -r requirements.txt
```

## 运行
```bash
python app.py
```

打开后在菜单 **File → Open Skin Folder...** 选择你的皮肤目录。

## 代码结构
```
core/
  skin_loader.py   # 解析 skin.ini 与素材发现（含@2x优先）
  osk_io.py        # .osk 导入/导出（zip）——骨架
  image_ops.py     # 图像处理（描边示例）
ui/
  main_window.py   # 主窗口（左：素材列表，右：预览标签页）
  preview/
    std_preview.py   # 标准模式预览
    mania_preview.py # mania 预览
```

> 后续扩展其他内容
