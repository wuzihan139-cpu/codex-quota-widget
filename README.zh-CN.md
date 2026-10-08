# codex-quota-widget

Windows 桌面置顶小浮窗，实时显示 [OpenAI Codex](https://openai.com/index/introducing-codex/)
（ChatGPT 计划）的**5 小时额度**与**每周额度**用量百分比、进度条和重置倒计时。

![screenshot](screenshot.png)

非官方工具——只读取 Codex CLI 自身使用的同一个用量接口。与 OpenAI 无关联。
[English](README.md)

## 下载

安装包放在 [Releases](https://github.com/wuzihan139-cpu/codex-quota-widget/releases/latest)，不放进 git 仓库。仓库首页右侧的 Releases 也可以点进去下载。

[下载 CodexQuotaWidget.exe](https://github.com/wuzihan139-cpu/codex-quota-widget/releases/latest/download/CodexQuotaWidget.exe)

本机需要已经用 Codex CLI 登录过。程序没有签名，第一次打开时 Windows SmartScreen 可能拦截，选择「更多信息」再「仍要运行」。

## 特性

- **5h / 周额度**：已用百分比 + 变色进度条（绿 <70% / 橙 <90% / 红 ≥90%）+
  重置倒计时（如 `1小时47分后 (16:59)`）。
- **零依赖**：纯 Python 标准库（tkinter + 系统自带 curl）。
- **免维护登录**：每次轮询前重读 `~/.codex/auth.json`，Codex CLI 刷新令牌后
  浮窗自动跟进，无需任何操作。
- **左键拖动**移动；**拖边缘/右下角**等比缩放（字号、进度条、留白联动，
  0.75×–3×）；**双击**折叠成一行；**右键**菜单（立即刷新 / 跟随 /
  固定跟随 / 剩余额度 / 折叠 / 透明度 / 代理设置 / 使用说明 / 退出）。「使用说明」打开说明，
  最上方是项目仓库链接，其中也写了怎么填代理。
- **跟随模式**：勾选"跟随 Codex 窗口"后，浮窗嵌入 Codex 窗口顶栏
  （随 DPI 自适应，最大化/贴顶同样适用），并切换为紧凑单行；Codex 没有
  可见窗口（关闭或最小化）时浮窗自动隐藏，重新出现立即回位继续跟随。
  拖动 Codex 窗口或它被其他窗口遮挡时浮窗也会暂时让位，松手/移开后
  立即回位。
- **固定跟随**：右键勾选"固定跟随"（与顶栏跟随互斥）。浮窗保持完整布局，
  并共享跟随模式的隐藏、拖动让位和遮挡恢复；遮挡按浮窗自身区域判断。
  开启时落到默认位（Codex 窗口左下角、头像上方不远处）。两套位置分开
  记忆：窗口化时跟随窗口移动和小幅缩放，拖动即重新钉住这一槽位；第一次
  最大化回到默认位，之后在最大化状态下的拖动单独记住。还原窗口回到
  窗口化槽位。双击折叠以标题为锚，位置不跳。
- **已用 / 剩余显示**：勾选"显示剩余额度"把数字与进度条切换为剩余额度；
  警告色始终按已用比例计算（剩 8% 依然是红色）。
- **托盘图标**：左键随时找回隐藏或卡住的浮窗；右键菜单（找回/立即刷新/
  退出），浮窗不可见时也能正常退出。
- **DPI 原生渲染**：声明 Per-Monitor DPI 感知，125%/150% 缩放屏上文字不发虚。
- 每 60 秒自动刷新；倒计时每 30 秒重绘。

## 环境要求

- Windows 10 / 11
- Python 3.8+（含 tkinter，正常安装的 Windows Python 都有）
- `curl.exe` —— 系统自带，无需安装
- 有 Codex 权限的 ChatGPT 计划，且至少登录过一次 `codex` CLI
  （这会生成 `~/.codex/auth.json`）

## 使用

1. 下载 `codex_quota_widget.pyw`、`start_widget.bat`，以及同级的
   `codex_widget/` 包到任意文件夹。
2. 双击 `start_widget.bat`（或直接双击 `.pyw`），浮窗出现在屏幕右下角。
3. 可选——开机自启：Win+R → `shell:startup` → 把 `start_widget.bat`
   的快捷方式放进去。

## 工作原理（以及为什么要用 curl）

每 60 秒：

1. 从 `~/.codex/auth.json` 读取 ChatGPT 访问令牌和账号 ID
   （文件不离开本机，令牌只发送给 `chatgpt.com`）；
2. 调用 `GET https://chatgpt.com/backend-api/codex/usage`，渲染
   `rate_limit.primary_window`（5h）/ `secondary_window`（周）的
   `used_percent`、`reset_at`、`limit_window_seconds`。

两个踩过的坑，供改造者参考：

- **Python 自带 TLS 栈会被 chatgpt.com 的 Cloudflare 403**（TLS 指纹识别），
  改用系统 `curl.exe`（Schannel）即可稳定通过。
- **不要**给 curl 加 `--ssl-no-revoke`——它会改变 Schannel 握手方式，同样
  被 403。原样使用即可。

网络顺序：先试代理，失败立即回落直连。没单独设置时用
`http://127.0.0.1:7897`（Clash 默认端口）。右键「代理设置」可填自己的地址，
写在本机 `~/.codex/widget_proxy.txt`，打包后的程序同样读取。环境变量
`CODEX_WIDGET_PROXY` 仍可用（设为 `direct` 强制直连）；本机文件优先。
代理没开时第一次尝试会瞬间失败（连接拒绝），回落几乎没有代价。

## 配置

| 项目 | 方法 |
| --- | --- |
| 代理 | 右键「代理设置」，或环境变量 `CODEX_WIDGET_PROXY`（默认 `http://127.0.0.1:7897`，`direct` = 仅直连）。本机文件优先 |
| 刷新间隔 | 改 `codex_widget/config.py` 里的 `POLL_SECONDS` |
| 调试日志 | 带 `--debug` 运行 → 在脚本旁写 `widget.log` |

## 打包（可选）

用 PyInstaller 可产出免安装的单文件 exe——图标与 spec 在 `packaging/`，
产物输出到 `app/`：

    python -m PyInstaller --onefile --noconsole --icon="<绝对路径>/packaging/app.ico" \
        --name CodexQuotaWidget --distpath app --workpath build \
        --specpath packaging codex_quota_widget.pyw

注意：icon 必须是绝对路径（相对路径会相对 spec 目录解析）。exe 未签名，
首次运行可能触发 SmartScreen（更多信息 → 仍要运行）。

## 常见问题

- **标题出现 ⚠ / 状态行报错**：下一轮会自动重试；`HTTP 401` 表示令牌过期，
  运行一次 `codex` 刷新登录即可，浮窗会自动用上新令牌。
- **某行显示 `--`**：服务端没返回该窗口的数据（如新账号），有数据后自动显示。
- **拖不动窗口**：抓住标题行（"Codex · Plus"）拖动。

## 许可

[MIT](LICENSE)
