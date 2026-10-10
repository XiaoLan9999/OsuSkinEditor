# 公告与程序更新发布维护

本文说明当前更新机制及发布顺序，不表示文中的示例版本已经发布

当前源码身份为 `VERSION = "1.6.0-preview.9"`、`BUILD_ID = "preview-r9"`、`BUILD_NUMBER = 9`、`CHANNEL = "preview"`，界面将 `preview` 显示为 Beta，将 `stable` 显示为正式版

## 用户如何更新

v1.5 和开发预览 r1–r4 没有程序更新入口，需要先手动下载并运行一次支持更新的 Windows EXE，公告不能给旧版远程安装更新功能

预览 r5 已有更新入口，但下载跳转编码存在兼容问题，也需要手动升级到 r6 一次

从 r9 开始，点击「关于」→「检查程序更新」立即检查同一份签名清单中的 Beta 与正式版，两行分别显示检查结果、当前可更新版本和「更新」按钮，无需选择通道或再点击检查

点击有新版的行内「更新」后先处理未保存的配置与皮肤设计，再自动下载、校验并重启完成安装，不再设置独立的下载与重启按钮，进度与取消在同一个紧凑窗口中显示，后台自动检查只提示，不自行安装

当前签名清单的正式版槽为 `null`，正式版行显示「暂无更新」，这不代表 v1.5 已纳入签名更新机制，也不能将当前 Beta 标记为正式版最新版

连接方式默认「自动选择」，可在默认折叠的「连接设置」中固定为 GitHub 直连、GHFast 或 GH-Proxy，选择保存在 `updates/source_mode`，在检查、下载或安装准备期间不可切换，当前线路文本显示实际获得有效内容的域名

「启动时显示新公告」控制公告是否自动弹出，「连接设置」中的启动自动检查控制后台联网检查，两项设置独立，关闭后仍可从菜单手动操作

从源码运行时只提供检查和下载，更新流程不会将 `python.exe` 或源码目录当作安装目标

## 数据位置与信任边界

| 文件或地址 | 用途 |
| --- | --- |
| `core/app_version.py` | 当前版本、公告 ID、构建号、默认通道与平台 |
| `assets/updates.json` | 随程序打包的当前构建离线说明，当前仅包含 r9 |
| `updates/announcements.json` | 完整中英双语公告历史，最新记录排在前面 |
| `updates/manifest.json` | 经过 Ed25519 签名的稳定版和预览版更新指针 |
| `core/update_public_key.py` | 程序内置的 Ed25519 公钥，可提交到源码仓库 |
| `tools/publish_update.py` | 本地生成并验证签名清单，不上传任何文件 |

客户端的原始数据地址固定为以下 HTTPS 地址，当前没有使用 GitHub Pages

- [在线公告历史](https://raw.githubusercontent.com/XiaoLan9999/OsuSkinEditor/main/updates/announcements.json)
- [签名更新清单](https://raw.githubusercontent.com/XiaoLan9999/OsuSkinEditor/main/updates/manifest.json)

公告用于展示文本，不能授权执行文件，程序更新只接受经过内置公钥验证的清单，清单签名覆盖 Base64 解码后的原始 UTF-8 JSON 字节，修改载荷后必须重新签名

清单按 `stable` 与 `preview` 分槽，每槽为一个版本对象或 `null`，版本对象包含 `version`、`build_id`、`build_number`、`channel`、`platform`、`url`、`sha256`、`size` 和 `notes_id`

下载地址必须属于本项目的 `https://github.com/XiaoLan9999/OsuSkinEditor/releases/download/<tag>/<exe>`，更新包大小不超过 512 MiB，客户端验证实际文件大小和 SHA-256 后才将下载结果交给更新流程

## 多线路检测与缓存

默认自动模式同时检测以下三个传输来源，GH-Proxy 的网站域名与其文档给出的资源代理前缀不同

| 连接方式 | 元数据与下载传输 | 服务说明 |
| --- | --- | --- |
| GitHub 直连 | 原始 GitHub / Raw 地址 | 本项目仓库与 Release |
| GHFast | `https://ghfast.top/<完整原始 HTTPS URL>` | [GHFast 网站](https://ghfast.top/) |
| GH-Proxy | `https://gh-proxy.org/<完整原始 HTTPS URL>` | [官方快速上手](https://gh-proxy.com/docs/quick-start) |

检测请求使用实际公告和签名清单，不以 DNS、TCP、首页或 HTTP 状态成功作为内容有效的依据，公告需要通过格式检查，程序更新清单需要通过内置公钥验签，HTML 错误页、损坏内容和伪造签名不能提供安装授权

收到首个有效响应后，客户端保留一个短暂收集窗口，再从有效响应中选择最新签名信息，超时、网络失败或验证失败的线路不阻断已经通过验证的结果，固定模式只使用用户选定的线路

缓存记录保留内容来源，ETag 与来源绑定，不能把 GitHub 返回的 ETag 发送给加速线路，也不能用另一线路的 304 响应给旧缓存续期，所有缓存清单再次读取时都要验签，断网时保留可用缓存和当前版本的离线说明

自动下载按照近期成功来源优先尝试，失败时回退到其他允许的线路，切换后清除本次未完成的文件、重新计算 SHA-256 并从头下载，进度也从 0 开始，不跨域拼接断点数据

签名清单的 `url` 始终保留原始 GitHub Release 地址，客户端只在传输层构造固定允许的加速地址，不接受公告或镜像返回的任意下载站，HTTPS、重定向范围、发布签名、文件大小和 SHA-256 检查始终生效

以上公共加速服务由第三方运营，本项目不承诺持续可用或特定地区速度，维护者后续可加入自己控制的国内 HTTPS 镜像，镜像只复制原始已签名清单与最终 EXE，不持有签名私钥，不能单独授权另一个更新包

## 构建号与公告 ID

`BUILD_NUMBER` 是全渠道统一的递增序号，每次发布都应大于正式版和开发预览中所有已经发布的构建号，不能在切换通道后重新从 1 开始，也不能只递增版本字符串

例如当前 Beta 构建号为 9，下一个正式版如果希望当前 Beta 用户能够升级，必须使用大于 9 的构建号，客户端不会因为版本标签看起来更高而安装相同或更低的构建号

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

下面以发布当前 r9 构建为例，示例不会展示或创建私钥，版本和构建号必须改成实际待发布构建的值

```powershell
$signingKey = Join-Path $env:LOCALAPPDATA 'OsuSkinEditor\Publisher\update-signing-key.pem'
$releaseExe = 'work\release\OsuSkinEditor-v1.6.0-preview.9-windows-x64.exe'
$manifestArgs = @(
    '--exe', $releaseExe,
    '--version', '1.6.0-preview.9',
    '--build-id', 'preview-r9',
    '--build-number', '9',
    '--channel', 'preview',
    '--url', 'https://github.com/XiaoLan9999/OsuSkinEditor/releases/download/v1.6.0-preview.9/OsuSkinEditor-v1.6.0-preview.9-windows-x64.exe',
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

用户点击「更新」且下载校验完成后，更新流程重新核对下载信息，自动启动独立助手，助手只处理本次指定的主程序及关联进程，不扫描或终止其他同名程序

助手先确认目标目录可以写入，再等待主程序退出，将旧 EXE 备份到同目录的 `<程序名>.exe.backup-<标识>.bak`，校验备份后替换原位置并启动新版

新版需要返回属于本次更新的启动确认，启动退出或确认超时时，助手尝试恢复旧 EXE 并重新启动旧版，备份不会因成功安装而自动删除

目录权限不足时不会自动提权，也不会强行改动原程序，网络失败、取消下载或校验失败不会进入替换流程

如果回滚也被文件占用等情况阻止，应保留旧版备份与失败记录，确认相关程序已经关闭后再恢复，更新目标不包含皮肤目录、用户设置或 Python 解释器
