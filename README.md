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

## 打包

在 Windows 上双击 `build.bat`，或执行：

```powershell
python -m pip install -r requirements.txt pyinstaller
pyinstaller --noconfirm --clean jev.spec
```

产物位于 `dist\jev-chat-windows\`。这是 PyInstaller 的 onedir 构建，运行时需要保留整个目录。`.github/workflows/release.yml` 使用 Windows runner 在 `v*` 标签推送时构建压缩包；手动触发工作流只生成构建 artifact。

## 已知限制

- OCR 依赖微信当前界面的像素布局、气泡颜色和字体。界面缩放、主题或微信版本变化可能影响识别。
- 会话名由 OCR 读取，相近名称可能被合并；同一发言人连续发送完全相同的文字可能被去重。
- 群聊发言人名漏识时，消息可能归属错误；`@名字` 只是普通文字。
- 候选经过解析与过滤后可能少于三条。模型判断和回复都需要人工确认。
- 聊天上下文与建议只保存在进程内存中，退出后不恢复。关闭悬浮窗即退出程序，目前没有系统托盘。
- Windows 10 的窗口采集提示黄框由系统显示；暂停采集后会消失。

