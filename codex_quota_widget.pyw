#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Codex 用量浮窗。

常驻桌面的置顶小窗，显示 Codex（ChatGPT 计划）的 5 小时额度 / 每周额度
用量百分比、进度条与重置倒计时。

数据来源: ~/.codex/auth.json 的登录令牌
        -> https://chatgpt.com/backend-api/codex/usage （每 60 秒刷新一次）
说明: chatgpt.com 的 Cloudflare 会拦截 Python 的 TLS 指纹（403），
     所以必须借助系统自带的 curl.exe（Schannel）发起请求。

用法: pythonw codex_quota_widget.pyw [--debug]
交互: 左键拖动移动；双击折叠/展开；右键菜单（立即刷新/跟随/固定跟随/显示模式/折叠/代理设置/使用说明/退出）。
托盘: 左键找回浮窗，右键菜单（找回/立即刷新/退出）。
代理: 右键「代理设置」写到 ~/.codex/widget_proxy.txt。未设置时用
      环境变量 CODEX_WIDGET_PROXY，再没有则 http://127.0.0.1:7897。
      direct 表示只直连。本机文件优先于环境变量。

实现按职责拆在同级目录 codex_widget/。本文件只是启动入口。
"""

from codex_widget.app import main

if __name__ == "__main__":
    main()
