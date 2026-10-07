"""读取 ~/.codex/auth.json，用 curl 拉取 Codex 额度，并格式化倒计时。

chatgpt.com 的 Cloudflare 会拦截 Python 的 TLS 指纹（403），必须借助
curl.exe（Schannel）。不要加 --ssl-no-revoke，那会改变握手并同样 403。
pythonw 没有控制台，subprocess 必须带 CREATE_NO_WINDOW，否则会闪黑框。
"""

import datetime
import json
import os
import shutil
import subprocess
import time

from codex_widget.config import AUTH_PATH, PROXY, USAGE_URL, log

# Git 自带的 curl（Schannel 较新，能过 chatgpt.com 的 Cloudflare）；
# 系统自带的 curl.exe 版本旧，会被 403，仅作后备。
_GIT_CURL = r"C:\Program Files\Git\mingw64\bin\curl.exe"
_CURL = [_GIT_CURL, r"C:\Windows\System32\curl.exe", shutil.which("curl")]


def curl_candidates():
    seen = []
    for c in _CURL:
        if c and os.path.exists(c) and c not in seen:
            seen.append(c)
    if not seen:
        raise RuntimeError("未找到 curl.exe")
    return seen


def load_auth():
    with open(AUTH_PATH, encoding="utf-8") as f:
        d = json.load(f)
    t = d.get("tokens") or {}
    return t.get("access_token"), t.get("account_id")


def run_curl(exe, proxy):
    access, account = load_auth()
    if not access:
        raise RuntimeError("未找到登录令牌，请先运行 codex 登录")
    cmd = [exe, "--silent", "--show-error",
           "--connect-timeout", "8", "--max-time", "20",
           "-w", "\n__HTTP__%{http_code}"]
    if proxy:
        cmd += ["-x", proxy]
    cmd += [USAGE_URL,
            "-H", "Authorization: Bearer " + access,
            "-H", "Accept: application/json",
            "-H", "User-Agent: codex_cli_rs/0.48.0"]
    if account:
        cmd += ["-H", "ChatGPT-Account-Id: " + account]
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=30,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if "__HTTP__" not in p.stdout:
        raise RuntimeError("curl 无响应: " + (p.stderr or "").strip()[:120])
    body, _, status = p.stdout.rpartition("\n__HTTP__")
    status = status.strip()
    if status != "200":
        raise RuntimeError("HTTP " + status + " " + body.strip()[:100])
    return json.loads(body)


def fetch_usage():
    """返回 (usage 字典, 通道标签)。依次尝试各 curl（代理 -> 直连）。"""
    tries = [PROXY, None] if PROXY and PROXY != "direct" else [None]
    errors = []
    for exe in curl_candidates():
        for proxy in tries:
            label = proxy or "直连"
            try:
                return run_curl(exe, proxy), label
            except Exception as e:
                errors.append("%s(%s): %s" % (
                    os.path.basename(os.path.dirname(exe)), label, e))
                log("fetch fail %s via %s: %s" % (exe, label, e))
    raise RuntimeError("；".join(errors))


def parse_window(w):
    if not isinstance(w, dict):
        return None
    reset = None
    ra = w.get("reset_at")
    if isinstance(ra, (int, float)):
        reset = float(ra)
    elif isinstance(ra, str):
        try:
            dt = datetime.datetime.fromisoformat(ra.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=datetime.timezone.utc)
            reset = dt.timestamp()
        except ValueError:
            pass
    if reset is None and w.get("reset_after_seconds") is not None:
        try:
            reset = time.time() + float(w["reset_after_seconds"])
        except (TypeError, ValueError):
            pass
    try:
        used = float(w.get("used_percent"))
    except (TypeError, ValueError):
        used = None
    return {"used": used, "reset": reset}


def reset_text(epoch):
    if epoch is None:
        return ""
    s = epoch - time.time()
    if s <= 0:
        return "已重置"
    dt = datetime.datetime.fromtimestamp(epoch)
    hm = dt.strftime("%H:%M")
    total_min = int(s // 60)
    days, rem = divmod(total_min, 1440)
    hours, mins = divmod(rem, 60)
    if days:
        return "%d天%d小时后 (周%s %s)" % (days, hours, "一二三四五六日"[dt.weekday()], hm)
    if hours:
        return "%d小时%02d分后 (%s)" % (hours, mins, hm)
    return "%d分后 (%s)" % (mins, hm)


def reset_text_compact(epoch):
    """跟随模式的精简倒计时：不带目标时刻和"后"字。"""
    if epoch is None:
        return ""
    s = epoch - time.time()
    if s <= 0:
        return "已重置"
    total_min = int(s // 60)
    days, rem = divmod(total_min, 1440)
    hours, mins = divmod(rem, 60)
    if days:
        return "%d天%d时" % (days, hours)
    if hours:
        return "%d时%02d分" % (hours, mins)
    return "%d分" % mins
