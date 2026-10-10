# OSK 导入、编辑与导出

## 从 osu!lazer 开始

在 osu!lazer 中导出想修改的皮肤，得到 `.osk`，再在编辑器点击「导入 OSK」或使用「文件 → 导入 OSK…」，也可以将包拖入窗口，导入快捷键为 `Ctrl+Shift+O`

osu!lazer 将用户内容保存在自己的管理存储中，操作导出的皮肤包比直接修改其内部文件更适合编辑器工作流，相关结构见 [官方用户文件存储说明](https://github.com/ppy/osu/wiki/User-file-storage)

OSK 是 ZIP 格式的皮肤包，lazer 的 [LegacySkinExporter](https://github.com/ppy/osu/blob/master/osu.Game/Database/LegacySkinExporter.cs) 使用 `.osk` 后缀，继承的 [LegacyArchiveExporter](https://github.com/ppy/osu/blob/master/osu.Game/Database/LegacyArchiveExporter.cs) 写出 ZIP 内容，另见 [官方 OSK 格式说明](https://osu.ppy.sh/wiki/en/Client/File_formats/osk_(file_format))

## 可编辑工作副本

导入成功后，程序直接打开可编辑副本，原始 OSK 不会被修改

工作副本持久保存在编辑器的应用数据目录下的 `imported-skins/`，不是关闭程序就删除的临时文件，标题区域显示原始包的名称，「文件夹」按钮可直接打开当前副本，最近打开也指向该副本

可以照常替换图片与音频、保存 Mania 配置，并用皮肤设计面板调整上隐、颜色与底部加高，所有修改写入工作副本或另存的设计副本

每次导入创建新的独立目录，重新导入同一个 OSK 不会覆盖先前编辑的副本

## 导出回游戏

使用「文件 → 导出 OSK…」或 `Ctrl+Shift+E`，选择新的 `.osk` 文件名，程序禁止覆盖导入来源的原始包

有未保存配置或尚未写入素材的设计时，导出前提供三种选择

| 选择 | 导出内容 |
| --- | --- |
| 应用修改并导出 | 保存当前配置，将视觉设计应用到新皮肤副本，再导出该副本 |
| 仅导出已保存内容 | 导出当前皮肤目录里已经写入磁盘的配置与素材，不加入仅存在于预览的修改 |
| 取消 | 保留编辑状态，返回工作台 |

导出的 OSK 使用皮肤内容作为包的根目录，包含需要的子目录、动画帧、音频和 JSON，不加入编辑器的配置历史与素材备份

将新 OSK 交给 osu!stable 或 osu!lazer 导入，回到游戏选择皮肤并确认效果，编辑器中的流速、BPM、测试排列和参考线只用于预览，不会写入游戏配置

## lazer 兼容范围

lazer 的 [SkinImporter](https://github.com/ppy/osu/blob/master/osu.Game/Skinning/SkinImporter.cs) 接受 `.osk`，读取 `skininfo.json`，并将各控件布局保存为 JSON，编辑器会原样保留这些文件以便重新导入 lazer

有效包没有 `skin.ini` 时，编辑器只在工作副本中添加最小传统配置，名称与作者可从有界读取的 `skininfo.json` 元数据取得，原始 JSON 不重写

旧版 lazer 曾将目录记录成普通零字节文件，重新导出时会与同名子目录冲突，[官方问题 #27540](https://github.com/ppy/osu/issues/27540) 和 [旧数据库的后续说明 #34070](https://github.com/ppy/osu/issues/34070#issuecomment-3052299167) 记录了这种情况，编辑器只在零字节条目存在路径大小写完全一致的子文件时将其作为目录占位符处理，原包保持原样，重新导出的 OSK 不会包含这个假文件，孤立的零字节文件仍保留，非零文件与大小写冲突仍拒绝

Standard / Mania 预览和 Mania 配置面板使用传统皮肤的 `skin.ini` 与图片素材，**并不是 lazer 原生 JSON 控件布局编辑器**，Argon 等原生皮肤没有传统素材的部分可能显示基础回退图形，游戏里的原生组件布局和 Mod 效果需要在 lazer 中确认

## 导入导出行为

- 只外包一层目录的有效皮肤包会自动去掉包装目录，皮肤自身的素材子目录保持原样
- 操作显示进度并支持取消，程序在后台处理文件，关闭窗口时会先请求取消再完成关闭
- 导入先完成内容检查和 CRC 校验再启用工作副本，失败或取消时清理本次半成品
- 导出先写临时包，检查完成后替换所选目标，失败或取消时保留目标原有内容
- 不接受指向目录外的路径、绝对路径、符号链接等危险条目，限制文件数量、单文件大小和展开总量，损坏或异常的大包会给出错误提示

默认限制为包文件最大 1 GiB、展开总量最大 1 GiB、单文件最大 100 MiB、条目最多 20,000 个，元数据 JSON 最多读取 1 MiB

已有皮肤文件夹仍可直接打开，OSK 导入提供的是新的工作副本入口，导出时不需要手动压缩或修改文件后缀
