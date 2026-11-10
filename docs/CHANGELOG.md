# 修改记录

本文件记录中期汇报前的代码/文档改动、验证方式和遗留问题。后续改动继续往下追加，不要改写历史条目。

## 2026-11-09 · 中期修复（分支 `midterm-fixes`）

### 修复

| # | 文件 | 改动 | 原因 |
|---|---|---|---|
| 1 | `app/overlay.py` | `SpinBox` 创建后设置 `lineEdit().setMinimumWidth(60)` | 当前环境 PySide6 6.12.0 + PySide6-Fluent-Widgets 1.11.3 下，SpinBox 内部 QLineEdit 宽度被压成 1px，设置页「参考上下文」显示空白 |
| 2 | `core/draft.py` | 新增 `_parrot()`；`_sanitize()` 增加「整条复读同一短语」过滤；`__main__` 自测补用例 | 原过滤只挡「候选原样等于对方消息」，实测「用那个词回我三遍」+「PING7」会放出 `PING7 PING7 PING7` |
| 3 | `tools/preview_ui.py` | 增加项目根 `sys.path` 引导；调试预览图改为仓库内 `docs/sample_chat.png` | 直接 `python tools/preview_ui.py` 会 `ModuleNotFoundError: app`；原调试图路径写死为 macOS 路径，Windows 上预览是黑底 |
| 4 | `tools/demo.py` | 增加项目根 `sys.path` 引导；新增 `--mock-jev` | 直接运行会找不到 `core`；没有 Jev key 的成员无法本地跑通三段式 |
| 5 | `tools/make_sample_chat.py` | 新增：用 PIL 生成 `docs/sample_chat.png`（合成聊天，不含真实数据） | 给 `preview_ui --state debug` 提供稳定、可提交的演示背景 |
| 6 | `tests/conftest.py`、`tests/test_core.py` | 新增 10 个离线 pytest 用例 | 原来只有各模块 `__main__` 自测，没有统一测试入口；覆盖候选解析、注入过滤、题目构造、三段式编排、脱敏、版本号、气泡颜色 |
| 7 | `requirements-test.txt` | 新增：`numpy`、`pytest` | 测试不依赖 Qt/OCR/SDK，CI 不必装完整运行依赖 |
| 8 | `.github/workflows/test.yml` | 新增：push/PR 触发，Windows runner 跑 `pytest -q` | 原 CI 只打包发版，不跑测试 |
| 9 | `README.md` | 恢复「隐私与边界」「什么会出网」「封号问答」「版权与许可」「免责声明」；新增「开发与测试」；项目结构表补 `tests/`、`docs/`、`tools/make_sample_chat.py` | 2026-11-01 的 README 重写把 512 行压到 84 行，隐私/伦理内容只剩一句话；中期答辩需要这一页 |

### 验证

```powershell
python -m pytest -q
# 10 passed

python core/providers.py
python core/llm.py
python core/jev_client.py
python core/draft.py
python core/engine.py
# 均输出 ok

python tools/preview_ui.py --state ready
python tools/preview_ui.py --state settings
python tools/preview_ui.py --state debug
# 三个窗口都能打开；设置页「参考上下文」显示 10；debug 预览有合成聊天背景

# 需要 LLM_API_KEY（DeepSeek 即可），不需要 JEV_API_KEY
python tools/demo.py --mock-jev
# 输出 3 条候选、推荐项、判断小抄
```

2026-11-09 真实微信窗口只读测试（未发送、未点填入）：WGC 帧 1186×1287，消息区和会话名识别成功；OCR 冷启约 1.7s、热态约 0.56s；测试后窗口已恢复最小化。

### 兼容性

- 测试环境：Windows 11、Python 3.12、PySide6 6.12.0、PySide6-Fluent-Widgets 1.11.3。
- 本次没有改依赖版本，也没有改 `core/engine.py` 的生产行为；`SpinBox` 修复是兼容性 workaround，换回旧版 PySide6 也不会坏。
- 如果全新安装后 SpinBox 仍异常，优先锁 `PySide6` / `PySide6-Fluent-Widgets` 版本，或保留这行 `setMinimumWidth`。

### 已知遗留 / 未做

- **生产版「无 Jev key 降级模式」还没做。** 目前没有 `JEV_API_KEY` 时，`main.py` 仍会提示配置模型；本地模拟请用 `python tools/demo.py --mock-jev` 或 `tools/preview_ui.py`，不要把 mock 当成真实判断质量。
- `_parrot()` 只挡「整条都在复读」；像 `PING7 说完了 该你了` 这种只出现一次 token 的混合句仍可能通过。是否要进一步做语义过滤，等中期后再定。
- 没有做真实用户研究；`docs/TEST_PLAN.md` 是小组成员的功能/演示测试，不是问卷。

### 回滚

所有改动都在 `midterm-fixes` 分支，未合并到 `main`。回滚：

```powershell
git checkout main
```

或对单个提交 `git revert <commit>`。改动不涉及数据库、配置格式或密钥，回滚无需迁移。

## 2026-11-10 · 合并可靠性分支 + 无 Jev 降级

### 合并

- 合并 `origin/codex/reliability-fixes`（`bebd2ab`）：回复新鲜度、会话隔离、OCR 相邻帧顺序去重、总时限、填入前核验、按会话偏好、边界/不确定状态。
- `.github/workflows/test.yml` 两边都新建，冲突解决为统一跑 `python -m pytest -q` 的 Python 3.10–3.12 矩阵。

### 修复

| # | 文件 | 改动 | 原因 |
|---|---|---|---|
| 1 | `core/engine.py` | `jev_key=""` 时跳过判断和排序，返回 `status="degraded"`、`ranking_status="unavailable"`，只保留起草阶段 | 没有 Jev key 时不再卡在“请配置模型”，候选仍可用 |
| 2 | `main.py` | 启动和 `start_pending` 只要求 `LLM_API_KEY`；缺 Jev 时提示降级并继续起草 | 生产版无 Jev key 降级模式 |
| 3 | `app/overlay.py` | Jev 密钥改为可选；设置页允许只填起草密钥；空态/状态栏显示降级说明；降级结果不显示推荐位 | 设置页原先强制两把 key，用户无法进入降级模式 |
| 4 | `tools/demo.py` | 无 Jev key 时自动降级；`--mock-jev` 仍走 mock 三段式 | 本地演示不需要 Jev key |
| 5 | `tools/preview_ui.py` | 新增 `--state degraded` 预览降级界面 | 汇报时可直接展示 UI |
| 6 | `tests/test_core.py`、`tests/test_reliability.py` | 增加降级引擎和“只填 LLM key 也能开始分析”的测试 | 回归保护 |

### 验证

```powershell
python -m pytest -q
# 38 passed

python -m compileall -q app core main.py tools tests
python tools/preview_ui.py --state ready
python tools/preview_ui.py --state settings
python tools/preview_ui.py --state degraded
```

### 已知遗留

- 没有真实 Jev key 时，降级候选没有意图、边界、紧张度和排序，需要用户自行判断。
- OCR 顺序恢复在满屏聊天滚到无重叠旧记录后可能不自动恢复；README/KICKOFF 已按实际行为标注，演示时切换会话可重建基线。
- 无 Jev 降级模式没有接入真实模型做端到端验收；本次用 mock/离线测试验证编排和 UI。
