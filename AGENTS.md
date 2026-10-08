# Codex Quota Widget — 项目上下文

本文件夹（`D:\agent\codex_quota_widget`）是独立的 ZCode 工作区，与
`D:\Study\sequence` 研究项目无关。此文件供未来会话自动读取，是本项目的
固化记忆。

## 项目是什么

Windows 桌面置顶小浮窗（tkinter，纯标准库）。入口仍是
`codex_quota_widget.pyw`（`start_widget.bat` 与 PyInstaller 都指向它），
实现按职责拆在同级包 `codex_widget/`：

- `config.py`：路径、轮询/代理/颜色常量、调试日志。`BASE_DIR` 是入口
  脚本或冻结 exe 的目录，不是包目录。
- `usage.py`：auth.json、curl 拉额度、窗口解析、倒计时文案。
- `codex_win.py`：进程快照、Codex 窗口发现、拖动探测、遮挡 z 序、工作区钳制。
- `follow.py`：顶栏跟随与固定跟随的两套槽位。
- `tray.py`：托盘线程。
- `dpi.py`：DPI 感知。
- `app.py`：浮窗界面、轮询泵、拖动/缩放/折叠。

浮窗实时显示 OpenAI Codex（ChatGPT 计划）的
5 小时/每周额度：百分比、进度条（警告色阈值绿<70%/橙<90%/红≥90%，
恒按已用比例算）、重置倒计时；右键可切换已用/剩余额度两种显示模式
（剩余模式数字与填充量 = 100−已用，警告色不变）。背景黑灰半透明
（#1e1e1e，窗口 alpha 0.96）。
`start_widget.bat` 启动；60 秒轮询；左键拖动、拖边缘等比缩放
（0.75–3×）、双击折叠（保持水平中心）、右键菜单（立即刷新/跟随/
固定跟随/显示剩余额度/折叠/使用说明/退出）；「使用说明」打开说明窗，
最上方是仓库链接 https://github.com/wuzihan139-cpu/codex-quota-widget
（仓库尚未公开）。托盘图标（左键找回浮窗，右键
找回/立即刷新/退出）——隐藏或卡住时唯一的可靠出口。

## 仓库状态（2026-10-07 已用 git 实况核对）

- GitHub：https://github.com/wuzihan139-cpu/codex-quota-widget（**private**，
  MIT，英文 README.md + 中文 README.zh-CN.md）。
- 分支 main 跟踪 origin/main。`beb4c08` 为固定跟随两套记忆槽位。
  其前为 `7c4859b` 托盘+打包，再前 `9a0206b` 跟随避让、`4464d4b` 跟随+显示、
  `72e7cc9` 标题居中、`65459d6` 初始发布。
- 其后提交（用户要求上传）：单文件拆成 `codex_widget/` 七个模块，入口
  脚本保留。行为不变，只改代码组织。提交 `6c978ea`。
- 用户已验收并要求打包上传：右键「使用说明」。说明窗置顶、无边框，
  顶部为仓库链接；正文按像素排满，英文和数字整段换行；说明窗里的点击
  不拖动、不折叠浮窗。`app/CodexQuotaWidget.exe` 按当前源码重新打包
  （仍 gitignore，不入库）。
- 用户已验收跟随目标两处（2026-10-08）：右键菜单不当跟随目标；标题里
  出现 codex 的终端不再跟随。此次未重新打包 exe。
- 用户审核通过之前不 commit/push（工作约定）；每轮改动遵循"先提交推送
  备份，再动工，不满意回退"的节奏。
- 冻结 exe 坑（托盘排障实录）：收句柄的 WinAPI 必须 64 位 argtypes
  （CreateWindowExW/LoadIconW/DefWindowProcW/GetModuleHandleW 已设，
  勿删），否则托盘线程 OverflowError 静默死亡；noconsole 下线程异常
  无痕，排障要 try/except + log(traceback)。
- 打包（2026-10-07 实验）：PyInstaller 单文件 exe——
  `python -m PyInstaller --onefile --noconsole --icon=".../packaging/app.ico"
  --name CodexQuotaWidget --distpath app --workpath build --specpath
  packaging codex_quota_widget.pyw`（icon 必须绝对路径，否则相对 spec
  目录解析）。产物 `app/CodexQuotaWidget.exe`（~11MB，已 gitignore）；
  `packaging/`（app.ico + spec）入库可复现。代码侧冻结适配：BASE_DIR 走
  `sys.frozen` 分支（否则 widget.log 写进临时解包目录），托盘优先用
  exe 内置图标。未签名 exe 首跑会遇 SmartScreen（更多信息→仍要运行）。

## 功能：跟随模式（2026-10-07 实现，用户已验收）

右键勾选"跟随 Codex 窗口"：每秒找 Codex 窗口，浮窗水平居中嵌入其
顶栏（垂直居中于窗口非客户区顶部；chatgpt/codex 是 Chromium 系
自绘标题栏，客户区=整窗即 nc_top=0，效果为覆盖其头部条且与窗口顶
齐平；y 不越出所在屏幕工作区顶边）。连续约 2 秒（2 拍，防进程快照
偶发失败闪隐）找不到 Codex 窗口则自动隐藏，重新出现立即定位并显形
跟随；关闭跟随时若已隐藏则强制显形。最小化视同消失。拖动 Codex 窗口
期间浮窗也立即隐藏（120ms 探针 `drag_target_exe()`：前台线程
GUITHREADINFO 的 GUI_INMOVESIZE 标志 + hwndMoveSize 解析归属，仅
codex/chatgpt/终端命中才隐藏，拖浮窗自身不触发），松手后立刻定位到
新位置顶端显形；拖动期间 1s 跟随节拍暂停，避免跟随拖影。Codex 顶栏
停靠区被其他可见窗口盖住时同样暂时消失（`occluder_over()` 以 Codex
hwnd 为锚沿 z 序 `GetWindow(GW_HWNDPREV)` 向上走，跳过自身/最小化/
DWM cloaked 幽灵窗口；不依赖 EnumWindows 全局顺序），遮挡移除后
下一拍恢复；拖动松手时若仍被遮挡则保持隐藏。**宿主追溯的跳过名单
含编辑器/IDE（code/code-insiders/devenv，2026-10-07 加）：用户在
VSCode 集成终端跑过 codex 会话，ChatGPT App 最小化时浮窗曾顺着
codex.exe 父链跟走了 VSCode 窗口——编辑器不算 Codex 宿主，ChatGPT
不可见时按规则自动隐藏。**固定跟随（右键勾选，与普通跟随互斥）：
开启时两个槽位都清空，浮窗落到默认位（左下角、头像上方：
`_PIN_DEFAULT_GAP=105` / `_PIN_DEFAULT_LEFT=8`），保持常规布局（不切
紧凑单行，可折叠/缩放）。两套记忆槽位互不影响（`_pin_pos` /
`_default_pin_pos`）：最大化槽位 `_pin_max` 第一次进入最大化用默认位，
之后记住该状态下的拖动；窗口化槽位 `_pin_custom` 跟随窗口移动和小幅
缩放，单拍尺寸跳变 >150px 视为最大化/还原切换拍并跳过位移增量，还原后
回到此槽位。槽位存底边（`_default_pin_pos` 返回顶边，写入时加高度），
折叠/展开不瞬移。手动拖动只更新当前状态对应的槽位。消失/重现/拖动避让
与普通跟随一致，遮挡判定以浮窗自身所在区域为准。折叠/展开以
"Codex·Plus"标题为锚（水平中心与顶边不变），并同步更新当前槽位。
**坑：`_last_rect` 必须在 `_sync_follow` 之后更新**（在之前更新会导致
位移增量恒为零、常规态不跟随窗口移动）；`_drag_tick` 松手路径依赖
`_sync_follow` 内的这次更新。**锚定方案探索史（2026-10-07 五轮，用户
逐一否决，勿重复提案）：纯像素偏移（最大化漂移）、纯比例（贴底 60px
被放大成 140px）、绝对屏幕位置（失去依附，用户回退）、按钉点自动选边的
混合（"略微往下"）→ 最终定为两套记忆槽位，默认位参照头像上方（GAP 由
140 调到 105）。**
普通跟随同时切换为紧凑单行（约 380×39）：`5h ▮67% 1时37分 │ 周 ▮10% 6天19时`，
无标题无状态行，百分比变色；出错时单行内容临时换成橙色错误提示。
跟随检测优先级（`find_codex_rect()`）：
1. Codex 桌面 App 自身窗口（codex.exe 拥有可见顶层窗口）；
2. **CLI 宿主窗口**：沿 codex.exe 父进程链找最近的有可见窗口的祖先——
   实测本机用户是在 **ChatGPT 桌面 App（chatgpt.exe）里跑 Codex**，
   命中的就是它；
3. 经典控制台 conhost（宿主在父链上）。
标题里出现 codex 的终端不再跟随：Grok Build / Windows Terminal 的标签
会带上任务名（例如 Codex quota widget），ChatGPT 最小化时浮窗会贴到
这个聊天窗口。真正承载 codex 的终端仍由父链命中。
系统进程（explorer 等）在链追溯中跳过；最小化窗口不跟随。右键菜单
（有 owner 的无标题弹出层，或带 WS_EX_NOACTIVATE 的弹出层）不跟随，
否则浮窗会跳到点击处。

## 关键技术事实（踩过坑的，勿回退）

- 接口 `GET https://chatgpt.com/backend-api/codex/usage`，Bearer 令牌来自
  `~/.codex/auth.json`（每次轮询前重读，CLI 刷新令牌后自动跟进）。
- **Python 自身 TLS 指纹被 chatgpt.com 的 Cloudflare 403**，必须用 curl
  子进程（系统/Git 自带 Schannel 版均可）；**curl 加 `--ssl-no-revoke`
  必被 403**（矩阵测试 6/6 验证），不要加。
- 网络：默认走 Clash `http://127.0.0.1:7897`，失败瞬间回落直连；
  环境变量 `CODEX_WIDGET_PROXY` 可覆盖（`direct`=仅直连）。
- pythonw 这类无控制台 GUI 进程 spawn curl 会闪黑框，必须
  `creationflags=CREATE_NO_WINDOW`。
- 用户屏幕 DPI 会变（144/120 都出现过）：启动时
  `SetProcessDpiAwareness(2)` + `tk scaling = dpi/72`，文字才不发虚。
- **tkinter 陷阱：对已 `pack_forget` 的控件调用 `pack_configure` 会把它
  重新显示**——`apply_scale` 里已用 `winfo_manager()=="pack"` 守卫，
  改布局逻辑时保持这个守卫。
- 混合 DPI 多显示器下 ImageGrab 截图坐标会偏移（截出来缺角是截图工具
  的问题，不是窗口问题）；要视觉验证就在主屏渲染测试实例再截。
- 本机窗口检测/进程枚举全部用 ctypes（Toolhelp32 快照 + EnumWindows），
  无第三方依赖。

## 工作约定

- 改动后先自验（数值验证 + 主屏渲染截图）再交用户审核；**用户审核通过
  之前不 commit/push**。
- `--debug` 运行写 `widget.log`（已 gitignore）。
