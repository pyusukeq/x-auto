#!/usr/bin/env python3
"""
メトリクス確認スクリプト
posted_tweets.json に記録された投稿のインプレッション・エンゲージメントを取得し
.company/secretary/notes/ にレポートを保存する

使い方: python pipeline/metrics.py
"""

import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import json
import os
import subprocess
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TWEETS_FILE = os.path.join(BASE_DIR, "pipeline", "posted_tweets.json")
NOTES_DIR = os.path.join(BASE_DIR, ".company", "secretary", "notes")


def get_metrics_via_mcp(tweet_id: str) -> dict:
    """
    Claude Code の x-twitter MCP 経由でメトリクスを取得する。
    このスクリプトはClaude Codeから呼ばれることを想定しているため、
    実際のMCP呼び出しはClaude Code側で行う。
    このファイルはメトリクスのフォーマット・保存を担当する。
    """
    # MCP呼び出しはClaude Codeが実行するため、
    # ここではプレースホルダーとして空dictを返す
    return {}


def format_report(tweets: list, metrics_data: dict, report_date: str) -> str:
    """メトリクスデータをMarkdownレポートに整形する"""

    lines = [
        f"---",
        f'date: "{report_date}"',
        f"type: metrics-report",
        f"---",
        f"",
        f"# インプレッションレポート - {report_date}",
        f"",
        f"| 投稿日 | 種別 | インプレッション | いいね | RT | クォート | ブックマーク |",
        f"|--------|------|----------------|--------|-----|----------|--------------|",
    ]

    total_impressions = 0
    total_likes = 0
    total_retweets = 0

    for tweet in tweets:
        tid = tweet["id"]
        m = metrics_data.get(tid, {})
        pub = m.get("public_metrics", {})

        imp = pub.get("impression_count", "-")
        like = pub.get("like_count", "-")
        rt = pub.get("retweet_count", "-")
        quote = pub.get("quote_count", "-")
        bookmark = pub.get("bookmark_count", "-")

        if isinstance(imp, int):
            total_impressions += imp
        if isinstance(like, int):
            total_likes += like
        if isinstance(rt, int):
            total_retweets += rt

        text_preview = tweet.get("text", "")[:25] + "..."
        lines.append(
            f"| {tweet['date']} | {tweet['type']} | {imp} | {like} | {rt} | {quote} | {bookmark} |"
        )

    lines += [
        f"",
        f"## 合計",
        f"- インプレッション: {total_impressions}",
        f"- いいね: {total_likes}",
        f"- RT: {total_retweets}",
        f"",
        f"## 注目ポイント",
        f"- インプレッションが高い投稿のフォーマット・トピックを次回に活かす",
        f"- いいね率（いいね÷インプレッション）が高い投稿が「刺さる」コンテンツ",
        f"",
        f"---",
        f"*集計時刻: {datetime.now().strftime('%Y-%m-%d %H:%M')}*",
    ]

    return "\n".join(lines)


def save_report(content: str, report_date: str):
    os.makedirs(NOTES_DIR, exist_ok=True)
    report_path = os.path.join(NOTES_DIR, f"{report_date}-metrics.md")

    # 同日ファイルがあれば追記、なければ新規作成
    if os.path.exists(report_path):
        with open(report_path, "a", encoding="utf-8") as f:
            f.write(f"\n\n---\n\n## 追加集計 ({datetime.now().strftime('%H:%M')})\n\n")
            f.write(content)
        print(f"既存ファイルに追記: {report_path}")
    else:
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"レポート保存: {report_path}")

    return report_path


def load_tweets() -> list:
    if not os.path.exists(TWEETS_FILE):
        print(f"ERROR: {TWEETS_FILE} が見つかりません")
        return []

    with open(TWEETS_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    return data.get("tweets", [])


def main():
    today = datetime.now().strftime("%Y-%m-%d")
    tweets = load_tweets()

    if not tweets:
        print("投稿ログが空です")
        return

    print(f"=== メトリクス確認 ===")
    print(f"対象: {len(tweets)}件の投稿\n")

    # 投稿IDの一覧を表示（Claude Codeが各IDのメトリクスを取得する）
    print("以下のツイートIDのメトリクスをClaude Codeに取得してもらいます：")
    for t in tweets:
        print(f"  [{t['date']}] {t['type']}: ID={t['id']}")

    print(f"""
Claude Codeへの指示:
  「pipeline/posted_tweets.json の全ツイートIDのメトリクスを取得して
    .company/secretary/notes/{today}-metrics.md にレポートを保存してください」
""")


if __name__ == "__main__":
    main()
