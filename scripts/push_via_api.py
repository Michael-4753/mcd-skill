# -*- coding: utf-8 -*-
"""
通过 GitHub REST API 推送文件（绕过 git:// HTTPS 被代理拦截的问题）。

用法：
    python push_via_api.py <owner> <repo> <local_dir>

原理：
    1. 为每个文件创建 blob（POST /git/blobs）
    2. 创建 tree（POST /git/trees）
    3. 创建 commit（POST /git/commits）
    4. 更新 ref（PATCH /git/refs/heads/main）
仓库为空时，步骤 4 需要先创建初始分支。

环境变量 GH_TOKEN 必须提供。
"""
import base64
import json
import os
import sys
import urllib.request
import urllib.error

API = "https://api.github.com"


def req(method, path, token, payload=None):
    url = f"{API}{path}"
    data = json.dumps(payload).encode() if payload is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header("Authorization", f"Bearer {token}")
    r.add_header("Accept", "application/vnd.github+json")
    r.add_header("X-GitHub-Api-Version", "2022-11-28")
    if data:
        r.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(r, timeout=60) as resp:
            body = resp.read().decode()
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        detail = e.read().decode()[:500]
        raise RuntimeError(f"{method} {path} -> {e.code}\n{detail}")


SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", ".idea", ".vscode"}
SKIP_EXT = {".pyc", ".pyo", ".log", ".swp", ".bak", ".tmp"}


def collect(root):
    files = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            if os.path.splitext(fn)[1].lower() in SKIP_EXT:
                continue
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            files.append((rel, full))
    return sorted(files)


def main():
    if len(sys.argv) != 4:
        print(__doc__)
        sys.exit(1)
    owner, repo, root = sys.argv[1], sys.argv[2], sys.argv[3]
    token = os.environ.get("GH_TOKEN", "").strip()
    if not token:
        print("缺少环境变量 GH_TOKEN")
        sys.exit(1)

    files = collect(root)
    print(f"待推送文件 {len(files)} 个\n")

    tree_entries = []
    for idx, (rel, full) in enumerate(files, 1):
        with open(full, "rb") as f:
            content = base64.b64encode(f.read()).decode()
        blob = req("POST", f"/repos/{owner}/{repo}/git/blobs", token,
                   {"content": content, "encoding": "base64"})
        tree_entries.append({
            "path": rel,
            "mode": "100644",
            "type": "blob",
            "sha": blob["sha"],
        })
        print(f"  [{idx}/{len(files)}] blob {rel}")

    tree = req("POST", f"/repos/{owner}/{repo}/git/trees", token,
               {"tree": tree_entries})
    print(f"\ntree sha: {tree['sha']}")

    # 读取 HEAD（仓库为空时为 null）
    ref = {}
    try:
        ref = req("GET", f"/repos/{owner}/{repo}/git/ref/heads/main", token)
    except RuntimeError:
        ref = {}
    parents = [ref["object"]["sha"]] if ref.get("object") else []

    commit = req("POST", f"/repos/{owner}/{repo}/git/commits", token, {
        "message": "feat: 麦麦多快好省 Skill 初始版本\n\n"
                   "基于麦当劳官方 MCP 的对话式套餐决策辅助 Skill，"
                   "以「更少的钱、买更好的食物、到手更快」为目标，\n"
                   "在预算内输出极致省钱/均衡最优/营养优先三套方案。\n\n"
                   "核心实现:\n"
                   "- nutrition_db.py: 158 条营养参考库(源自 MCP 实拉 160 条去重)\n"
                   "- nutrition_needs.py: Mifflin-St Jeor + 2023 版 DRIs 需求区间计算\n"
                   "- solve_engine.py: 五维加权评分 + 组合枚举 + 三方案互异化求解引擎\n\n"
                   "关键设计:\n"
                   "- 实价验证为代码级约束而非提示词约束\n"
                   "- 单餐区间折算(meal_ratio=1/3)避免热量维度失效\n"
                   "- nutritionDescription 异常值原样保留,不做数据清洗\n\n"
                   "Token 仅以 ${MCD_MCP_TOKEN} 环境变量占位符出现,不进仓库。",
        "tree": tree["sha"],
        "parents": parents,
    })
    print(f"commit sha: {commit['sha']}")

    if ref.get("object"):
        req("PATCH", f"/repos/{owner}/{repo}/git/refs/heads/main", token,
            {"sha": commit["sha"], "force": False})
        print("已更新 main 分支")
    else:
        req("POST", f"/repos/{owner}/{repo}/git/refs", token,
            {"ref": "refs/heads/main", "sha": commit["sha"]})
        print("已创建 main 分支")

    print(f"\n完成：https://github.com/{owner}/{repo}")


if __name__ == "__main__":
    main()