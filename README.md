# JevChat-Windows

Windows 微信聊天回复辅助工具。程序截取当前微信窗口，在本地识别聊天文字，调用配置的模型生成回复建议，并将选中的文字填入微信输入框。发送操作始终由用户完成。

## 功能

- 识别 Windows 微信主窗口（`Weixin.exe` / `WeChat.exe`），使用 Windows Graphics Capture 截图、RapidOCR 离线识别中英文消息。
- 根据气泡颜色区分自己与对方的消息，识别群聊发言人，并按会话保存本次运行期间的聊天上下文和建议。
- 收到对方的新消息时，先调用 Jev 判断对话，再调用语言模型起草最多三条候选，最后由 Jev 排序；展示建议动作、意图、需要和紧张度。
- 支持指定群聊回复对象，按对象重新生成建议；填入时可附加纯文本 `@名字`，这不会触发微信的正式 @ 提醒。
- 在置顶悬浮窗中查看候选、复制或填入、浏览识别到的聊天记录、暂停采集，并打开调试视图检查 OCR 框。
- 设置关系背景、说话风格、参考上下文条数（3–30）、起草思考模式及模型来源；可选择是否启动时检查新版本。

## 环境要求

- Windows 10 1903 或更新版本，或 Windows 11。
- Python 3.10–3.12。`rapidocr-onnxruntime` 1.4.x 不支持 Python 3.13 及更新版本。
- 已打开的微信主窗口。窗口可以被遮挡，但不能保持最小化；程序检测到最小化后会无激活还原并置于底层。
- 两个 API 密钥：`JEV_API_KEY` 用于 Jev 判断，`LLM_API_KEY` 用于语言模型起草。程序首次启动时可在设置页填写。

## 源码运行

在项目目录中执行：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python main.py
```

首次运行会打开设置页。选择 Jev 来源和起草模型来源，填入两把对应的密钥及模型 ID，再保存设置。Jev 可通过 OpenRouter 或 TypeSafe 直连；起草支持 OpenAI、Anthropic、Gemini 三种接口协议及相应的预设或自定义地址。没有默认模型 ID 的来源，需要手动输入或使用“获取模型”。来源配置见 [`core/providers.py`](core/providers.py)。

普通设置写入项目目录下的 `config.json`，该文件已被 Git 忽略。密钥写入当前 Windows 用户的环境变量（注册表 `HKCU\Environment`），不会写入 `config.json`。更换模型来源时，同一个密钥槽位会被新值覆盖。

## 处理流程

```text
微信窗口截图
  -> 像素定位消息区域与会话标题
  -> RapidOCR 识别并按气泡颜色区分发言人
  -> 按会话去重，仅在对方产生新消息时分析
  -> Jev 判断 -> 语言模型起草 -> Jev 排序
  -> 悬浮窗展示候选 -> 用户复制或填入 -> 用户手动发送
```

截图和 OCR 在独立子进程中运行，模型调用在线程中运行，界面由 Qt 主线程更新。截图、调试画面及聊天记录只保存在内存中；分析时，最近的聊天文本、关系背景和用于模仿语气的样本会发送到用户选择的模型接口。启动更新检查只请求 GitHub Releases 的版本信息，可在设置中关闭。

## 隐私与边界

这是个人自用工具，下面几条是硬约束，代码里就是这么写的：

- **只读自己电脑上、自己本来就有权查看的对话。** 不代替任何人查看别人的聊天。
- **只截自己的聊天窗口 + 本地离线 OCR（RapidOCR）。** 不 hook、不注入、不读对方数据库、不解密、不碰对方进程内存。
- **截图只在内存里。** 捕获到的帧是 numpy 数组，全程不写磁盘、不进日志、不上传；`app/`、`core/` 里没有 `.save()`。`probe/`、`tools/` 下的开发脚本（人工排查、预览界面用的）可能把图存成文件，但这些脚本不在发布包里。
- **调试视图也只在内存里画。** 那个窗口拿到的是子进程缩小后的同一份内存帧（走进程队列，不落盘），画完就丢，不存图、不进日志、不上传；关掉开关子进程连帧都不发。
- **绝不自动发送。** 只把文字粘进输入框就停手，不发回车、不点发送按钮。发不发、改不改，你来定。
- **不碰钱。** 转账、红包、收款相关的界面元素一律不碰，起草的 system prompt 里也禁了这几个话题。
- **只有对方的新消息到来（或你在群里换了回复对象）才调一次模型。** 静默期零调用——十分钟没人说话就是十分钟零 token。
- **API key 只进环境变量，而且全程只有两个。** `JEV_API_KEY`（判断）和 `LLM_API_KEY`（起草），不管来源选哪家都是这两个槽。都写进注册表 `HKCU\Environment`（跟 `setx` 同一个地方），任何文件里都不出现 key，也绝不进日志（报错文本一律脱敏）。老版本按来源分开存的 `OPENROUTER_API_KEY` / `DEEPSEEK_API_KEY` 仍然能读到，保存一次就迁到新名字上。
- **启动时查一次版本号（可关）。** 只向 GitHub Releases API 发一个 GET，带的只有 UA 和当前版本号，不夹带任何聊天内容；设置里「启动时检查更新」关掉就完全不发这个请求。

什么会出网：判断（`JEV_API_KEY`）去你选的 OpenRouter 或 TypeSafe 直连；起草（`LLM_API_KEY`）发给
你在设置里选的那家接口（DeepSeek 官网、OpenRouter、OpenAI、Moonshot、智谱、通义、硅基流动、
OpenCode Go、Anthropic、Gemini，或者自填的 OpenAI 兼容 / Anthropic 兼容地址），加上启动时（可关）一次到
GitHub 查版本号。**本项目没有任何自建服务器**，聊天内容只在触发分析的那一刻，发给你自己在设置里
配置的那个接口，本项目不收集、不落盘、不进日志。发出去的内容固定是：**最近 N 条对话文本**（N =
设置里的「参考上下文」，默认 10；群聊带发言人名）、**关系设置**、**你自己最近 12 条 60 字以内的短
消息**（当口吻样本，链接和长段不送）、**你填的说话风格**，群聊指定了回复对象的话再加一个对象名。
除此之外没有别的。OCR 全程离线。GitHub 版本查询只带 UA 和当前版本号，不夹带任何聊天内容。

**会不会因此被封号？** 本项目不 hook、不注入、不读对方的数据库或进程内存、不调用对方的任何
私有接口或账号体系——只截自己这一个窗口的画面做 OCR，跟读屏软件、录屏软件是同一类操作。

## 项目结构

| 路径 | 用途 |
| --- | --- |
| `main.py` | 启动界面和采集子进程，按会话维护上下文并触发分析 |
| `app/capture.py` | 查找微信窗口、截图和定位消息区域 |
| `app/ocr.py` | 本地 OCR、发言人分类与消息去重 |
| `app/worker.py` | 截图及 OCR 子进程 |
| `app/overlay.py`、`app/debugwin.py` | 悬浮窗、设置和识别调试视图 |
| `app/fill.py` | 使用剪贴板把候选填入输入框，不发送 |
| `app/settings.py` | 保存配置及读取密钥 |
| `core/engine.py` | 判断、起草和排序流程 |
| `core/providers.py`、`core/llm.py`、`core/jev_client.py` | 模型来源与接口调用 |
| `core/questions.py`、`core/draft.py` | 判断问题、起草提示词与候选过滤 |
| `tools/`、`probe/` | 界面预览、端到端示例及开发探针，不属于日常运行流程 |
| `tools/make_sample_chat.py` | 生成调试预览用的合成聊天图（不含真实聊天） |
| `tests/` | 离线 pytest 自测，不联网、不需要 key |
| `docs/TEST_PLAN.md`、`docs/CHANGELOG.md` | 小组成员测试方案与改动记录 |

## 打包

在 Windows 上双击 `build.bat`，或执行：

```powershell
python -m pip install -r requirements.txt pyinstaller
pyinstaller --noconfirm --clean jev.spec
```

产物位于 `dist\jev-chat-windows\`。这是 PyInstaller 的 onedir 构建，运行时需要保留整个目录。`.github/workflows/release.yml` 使用 Windows runner 在 `v*` 标签推送时构建压缩包；手动触发工作流只生成构建 artifact。

## 开发与测试

离线自测（不联网、不需要 key）：

```powershell
python -m pytest -q
```

UI 预览（合成数据，不采集、不联网、不操作真实微信）：

```powershell
python tools/preview_ui.py --state ready
python tools/preview_ui.py --state settings
python tools/preview_ui.py --state debug

# 没有 Jev key 时，用本地 mock 跑通三段式（起草仍走 LLM_API_KEY）
python tools/demo.py --mock-jev
```

调试预览的合成聊天图由 `python -m tools.make_sample_chat` 生成。小组成员的功能测试步骤、通过标准和分工见 [`docs/TEST_PLAN.md`](docs/TEST_PLAN.md)，本次改动记录见 [`docs/CHANGELOG.md`](docs/CHANGELOG.md)。

## 已知限制

- OCR 依赖微信当前界面的像素布局、气泡颜色和字体。界面缩放、主题或微信版本变化可能影响识别。
- 会话名由 OCR 读取，相近名称可能被合并；同一发言人连续发送完全相同的文字可能被去重。
- 群聊发言人名漏识时，消息可能归属错误；`@名字` 只是普通文字。
- 候选经过解析与过滤后可能少于三条。模型判断和回复都需要人工确认。
- 聊天上下文与建议只保存在进程内存中，退出后不恢复。关闭悬浮窗即退出程序，目前没有系统托盘。
- Windows 10 的窗口采集提示黄框由系统显示；暂停采集后会消失。

## 版权与许可

Copyright © 2026 rezoch340 与 jev-chat 贡献者。代码以 [MIT](LICENSE) 协议开源，另见 [NOTICE](NOTICE)。

- 本项目是 [Jev 聊天助手](https://github.com/jev-chat/jev-chat-jarvis)（安卓原版）的 Windows 姊妹项目，Jev 判断内核与题目口径来自上游，版权归 Finderchangchang 与 jev-chat 贡献者所有。
- 分发或商用时须保留 LICENSE 与 NOTICE，并在产品「关于」页、说明文档或发布页写明来源。推荐写法：`基于 JevChat-Windows（https://github.com/jev-chat/jev-chat-windows）二次开发`。
- 不要用「JevChat-Windows」「Jev 聊天助手」「jev-chat」名称或 chatjevs.com 域名暗示由原作者出品或背书。

**第三方组件与商用**：本项目自己的代码是 MIT，但 Windows 发布包（PyInstaller 打的 zip）里打进了 [PySide6-Fluent-Widgets](https://qfluentwidgets.com/)，该组件是 GPLv3 协议，非商用免费；商用需要向作者购买商业授权。因此发布包整体受 GPLv3 约束。其余依赖的许可证见 [NOTICE](NOTICE)。

**免责声明**：本项目只处理你自己设备上、你自己有权查看的聊天。请在自己设备上自用；装到别人机器上读别人的聊天记录是另一回事，本项目不为那种用法背书。请遵守对方软件许可协议与当地法律法规，对方改版可能导致本项目的界面识别失效。使用本项目造成的后果由使用者自行承担，作者不负责。

