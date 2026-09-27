# 公告与程序更新发布维护

本文说明当前更新机制及发布顺序，不表示文中的示例版本已经发布

当前源码身份为 `VERSION = "1.6.0-preview.5"`、`BUILD_ID = "preview-r5"`、`BUILD_NUMBER = 5`、`CHANNEL = "preview"`，已发布 v1.5 的链接保留在 README

## 用户如何更新

v1.5 和开发预览 r1–r4 没有程序更新入口，需要在支持更新的新版发布后，先手动下载并运行一次新版 Windows EXE，公告不能给旧版远程安装更新功能

之后使用「关于」→「检查程序更新」选择通道、检查、下载并重启更新，后台自动检查可以关闭，检查到新版本不会自行安装

「启动时显示新公告」控制公告是否自动弹出，「启动后自动检查公告和程序更新」控制后台联网检查，两项设置独立，关闭后仍可从菜单手动操作

从源码运行时只提供检查和下载，更新流程不会将 `python.exe` 或源码目录当作安装目标

## 数据位置与信任边界

| 文件或地址 | 用途 |
| --- | --- |
| `core/app_version.py` | 当前版本、公告 ID、构建号、默认通道与平台 |
| `assets/updates.json` | 随程序打包的当前构建离线说明，当前仅包含 r5 |
| `updates/announcements.json` | 完整中英双语公告历史，最新记录排在前面 |
| `updates/manifest.json` | 经过 Ed25519 签名的稳定版和预览版更新指针 |
| `core/update_public_key.py` | 程序内置的 Ed25519 公钥，可提交到源码仓库 |
| `tools/publish_update.py` | 本地生成并验证签名清单，不上传任何文件 |

客户端固定读取以下 HTTPS 地址，当前没有使用 GitHub Pages

- [在线公告历史](https://raw.githubusercontent.com/XiaoLan9999/OsuSkinEditor/main/updates/announcements.json)
- [签名更新清单](https://raw.githubusercontent.com/XiaoLan9999/OsuSkinEditor/main/updates/manifest.json)

公告用于展示文本，不能授权执行文件，程序更新只接受经过内置公钥验证的清单，清单签名覆盖 Base64 解码后的原始 UTF-8 JSON 字节，修改载荷后必须重新签名

清单按 `stable` 与 `preview` 分槽，每槽为一个版本对象或 `null`，版本对象包含 `version`、`build_id`、`build_number`、`channel`、`platform`、`url`、`sha256`、`size` 和 `notes_id`

下载地址必须属于本项目的 `https://github.com/XiaoLan9999/OsuSkinEditor/releases/download/<tag>/<exe>`，更新包大小不超过 512 MiB，客户端验证实际文件大小和 SHA-256 后才将下载结果交给更新流程

## 构建号与公告 ID

`BUILD_NUMBER` 是全渠道统一的递增序号，每次发布都应大于正式版和开发预览中所有已经发布的构建号，不能在切换通道后重新从 1 开始，也不能只递增版本字符串

例如当前预览构建号为 5，下一个正式版如果希望当前预览用户能够升级，必须使用大于 5 的构建号，客户端不会因为版本标签看起来更高而安装相同或更低的构建号

合并旧清单时，发布工具会比较两个通道的最大构建号，拒绝相同或更低的构建号，维护者还需确保源码中的构建身份与最终 EXE 一致

每个新构建使用唯一的 `BUILD_ID`，将对应公告加入 `updates/announcements.json`，并把该构建的离线说明放入 `assets/updates.json`，更新包的 `notes_id` 默认与 `build_id` 相同

## 签名私钥

发布私钥保存在仓库外的 `%LOCALAPPDATA%\OsuSkinEditor\Publisher\update-signing-key.pem`，仅用于本机发布步骤，不写入仓库、Release 附件、源码 ZIP、程序资源或命令输出

保留私钥的本地访问权限限制和安全备份，客户端只包含公钥，发布工具接受原始 32 字节 Ed25519 私钥或未加密的 PKCS8 PEM 私钥，并拒绝使用位于项目仓库内的私钥

更换公钥需要单独制定旧客户端的迁移方案，直接替换仓库中的公钥不能让已经安装的程序信任另一把私钥签出的清单

## 先构建助手，再构建主程序

以下命令在项目根目录执行，使用项目虚拟环境

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
.\.venv\Scripts\python.exe -m pip install pyinstaller
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --distpath dist/updater OsuSkinUpdater.spec
.\.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean OsuSkinEditor.spec
```

`requirements-lock.txt` 包含签名验证所需的 cryptography，当前锁定环境使用 Python 3.13

助手产物为 `dist/updater/OsuSkinUpdater.exe`，主程序产物为 `dist/OsuSkinEditor.exe`，主程序规格会将助手嵌入资源，缺少助手时构建会停止

如果助手位于其他目录，可先设置 `OSUSKIN_UPDATER_PATH` 指向已构建的助手 EXE，再构建主程序，发布给普通用户时只需要提供最终主程序 EXE

## 发布顺序

1. 更新 `core/app_version.py` 和中英双语公告，确认构建号、公告 ID、通道与将要发布的版本一致
2. 完成测试与打包，在副本目录验证当前 EXE 的启动、公告、检查更新、下载校验，以及更新助手的成功和失败恢复路径
3. 为最终 EXE 使用带版本的文件名，将它上传到本项目对应的 GitHub Release，预览构建标记为 prerelease
4. 确认上传文件可以下载，并核对下载内容与本地待签名 EXE 一致，此后不要重新构建或覆盖该版本的 EXE
5. 使用本地私钥对最终 EXE 的更新信息签名，存在旧清单时先验证旧清单并保留另一通道
6. 最后将签名清单和公告历史提交到 `main`，让客户端看到可下载的新版本，发布前检查候选文件中没有私钥、缓存、日志或个人皮肤

不要先发布指向尚未上传文件的新清单，也不要手动修改已经签名的 Base64 载荷，发布工具本身不执行上传或 Git 操作

下面以首次发布当前 r5 构建为例，示例不会展示或创建私钥，版本和构建号必须改成实际待发布构建的值

```powershell
$signingKey = Join-Path $env:LOCALAPPDATA 'OsuSkinEditor\Publisher\update-signing-key.pem'
$releaseExe = 'work\release\OsuSkinEditor-v1.6.0-preview.5-windows-x64.exe'
$manifestArgs = @(
    '--exe', $releaseExe,
    '--version', '1.6.0-preview.5',
    '--build-id', 'preview-r5',
    '--build-number', '5',
    '--channel', 'preview',
    '--url', 'https://github.com/XiaoLan9999/OsuSkinEditor/releases/download/v1.6.0-preview.5/OsuSkinEditor-v1.6.0-preview.5-windows-x64.exe',
    '--private-key', $signingKey,
    '--output', 'updates\manifest.json'
)
if (Test-Path -LiteralPath 'updates\manifest.json') {
    $manifestArgs += @('--existing', 'updates\manifest.json')
}
.\.venv\Scripts\python.exe tools\publish_update.py @manifestArgs
```

运行示例前，应将已经上传并核对过的最终 EXE 放在 `$releaseExe` 指定位置，`--existing` 会使用私钥派生的公钥验证旧清单，拒绝合并未签名或由其他密钥签名的数据

输出文件已经存在时必须将它同时传给 `--existing`，不能省略验证直接覆盖，发布工具会计算 EXE 的 SHA-256 与大小、写入 UTC 发布时间、签名并再次验证结果

## 替换、备份与失败恢复

用户选择「重启并更新」后，更新流程重新核对下载信息，启动独立助手，助手只处理本次指定的主程序及关联进程，不扫描或终止其他同名程序

助手先确认目标目录可以写入，再等待主程序退出，将旧 EXE 备份到同目录的 `<程序名>.exe.backup-<标识>.bak`，校验备份后替换原位置并启动新版

新版需要返回属于本次更新的启动确认，启动退出或确认超时时，助手尝试恢复旧 EXE 并重新启动旧版，备份不会因成功安装而自动删除

目录权限不足时不会自动提权，也不会强行改动原程序，网络失败、取消下载或校验失败不会进入替换流程

如果回滚也被文件占用等情况阻止，应保留旧版备份与失败记录，确认相关程序已经关闭后再恢复，更新目标不包含皮肤目录、用户设置或 Python 解释器
