#!/usr/bin/env python3
"""
下書きMarkdown生成スクリプト
scheduled/YYYY-MM-DD.json → draft/YYYY-MM-DD.md
"""

import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import json
import os
import sys
from datetime import datetime, timezone, timedelta

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PIPELINE_DIR = os.path.join(BASE_DIR, "pipeline")
sys.path.insert(0, PIPELINE_DIR)
from draft_html import save_daily_html
SCHEDULED_DIR = os.path.join(PIPELINE_DIR, "scheduled")
DRAFT_DIR = os.path.join(BASE_DIR, "draft")


def main():
    today = datetime.now().strftime("%Y-%m-%d")
    scheduled_path = os.path.join(SCHEDULED_DIR, f"{today}.json")

    if not os.path.exists(scheduled_path):
        print(f"ERROR: {scheduled_path} が見つかりません")
        sys.exit(1)

    with open(scheduled_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    posts = data.get("posts", [])
    types = data.get("types", ["速報", "解説", "事例"])
    fallback_post = data.get("fallback_post")
    review_failed = data.get("review_failed", [])

    jst = datetime.now(timezone(timedelta(hours=9)))
    time_str = jst.strftime("%H:%M JST")

    lines = [f"# X投稿下書き {today}", f"生成: {time_str}", ""]

    for i, post in enumerate(posts):
        label = types[i] if i < len(types) else f"投稿{i+1}"
        failed = review_failed[i] if i < len(review_failed) else False
        status = " ⚠️ レビュー未通過（投稿スキップ推奨）" if failed else ""
        lines.append(f"## 投稿{i+1}（{label}）{status}")
        lines.append("")
        lines.append(post)
        lines.append("")
        lines.append("---")
        lines.append("")

    if fallback_post:
        fallback_failed = data.get("fallback_review_failed", False)
        status = " ⚠️ レビュー未通過" if fallback_failed else ""
        lines.append(f"## 予備投稿（使用任意）{status}")
        lines.append("")
        lines.append(fallback_post)
        lines.append("")

    os.makedirs(DRAFT_DIR, exist_ok=True)
    draft_path = os.path.join(DRAFT_DIR, f"{today}.md")
    with open(draft_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f"下書き保存: {draft_path}")
    print(f"投稿数: {len(posts)}本")

    fallback_failed = data.get("fallback_review_failed", False)
    save_daily_html(posts, types, review_failed, fallback_post, fallback_failed, today)


if __name__ == "__main__":
    main()
