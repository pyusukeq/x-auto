#!/usr/bin/env python3
"""
投稿レビュー・修正エージェント
scheduled/{today}.json の投稿を審査し、問題があれば Claude API で自動修正する
修正できない場合は review_failed フラグを立てる（投稿はスキップされる）

検出する問題:
- 👇/⏬ があるがURLがない（「確認👇」だがリンクなし）
- ハッシュタグ使用（ルール違反）
- 文字数超過
"""

import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import json
import os
from datetime import datetime

import anthropic

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PIPELINE_DIR = os.path.join(BASE_DIR, "pipeline")
SCHEDULED_DIR = os.path.join(PIPELINE_DIR, "scheduled")

REVIEW_SYSTEM_PROMPT = """あなたはX投稿の品質管理担当者です。
指摘された問題点を修正した投稿テキストのみを出力してください。
説明・コメント・コードブロックは不要です。"""


def weighted_len(text: str) -> int:
    return sum(2 if ord(c) > 127 else 1 for c in text)


def check_post(post: str) -> list:
    """投稿の問題点を検出する。問題点リストを返す（空なら問題なし）"""
    issues = []

    has_arrow = '👇' in post or '⏬' in post
    has_url = 'http://' in post or 'https://' in post
    if has_arrow and not has_url:
        issues.append("👇/⏬ が使われているがURLがない（リンクなしのCTA）")

    for line in post.split('\n'):
        if line.strip().startswith('#'):
            issues.append(f"ハッシュタグが含まれている: {line.strip()[:40]}")
            break

    wlen = weighted_len(post)
    if wlen > 900:
        issues.append(f"文字数超過: {wlen}w（上限900w）")

    return issues


def revise_post(post: str, issues: list, source_context: str, client) -> str:
    """Claude API で問題のある投稿を修正する"""
    issues_text = "\n".join(f"- {issue}" for issue in issues)

    prompt = (
        f"以下のX投稿に問題が見つかりました。修正した投稿テキストを出力してください。\n\n"
        f"【元の投稿】\n{post}\n\n"
        f"【問題点】\n{issues_text}\n\n"
        f"【元記事の情報（URL参考）】\n{source_context}\n\n"
        f"修正ルール:\n"
        f"- 👇/⏬ の後にURLがない場合: 元記事のURLを追加するか、CTAを「保存推奨」「試してみて👀」などに変更\n"
        f"- ハッシュタグは削除\n"
        f"- 投稿の構造・口調・情報はできるだけ維持\n"
        f"- 全角換算350文字以内に収める"
    )

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        system=REVIEW_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}]
    )
    return message.content[0].text.strip()


def main():
    today = datetime.now().strftime("%Y-%m-%d")
    scheduled_path = os.path.join(SCHEDULED_DIR, f"{today}.json")
    collected_path = os.path.join(PIPELINE_DIR, "collected", f"{today}.json")

    if not os.path.exists(scheduled_path):
        print(f"ERROR: {scheduled_path} が見つかりません")
        sys.exit(1)

    with open(scheduled_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    source_context = "(元記事情報なし)"
    if os.path.exists(collected_path):
        with open(collected_path, "r", encoding="utf-8") as f:
            collected = json.load(f)
        stories = collected.get("top_stories", [])[:5]
        source_context = "\n".join(
            f"- {s['title'][:60]}: {s.get('url') or s.get('external_url', '')}"
            for s in stories
        )

    posts = list(data.get("posts", []))
    types = data.get("types", [])
    fallback_post = data.get("fallback_post")

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    client = None
    if api_key:
        import httpx
        client = anthropic.Anthropic(
            api_key=api_key,
            http_client=httpx.Client(timeout=httpx.Timeout(60.0, connect=15.0)),
        )

    print(f"=== 投稿レビュー開始 ({today}) ===\n")

    review_failed = [False] * len(posts)
    fallback_failed = False

    review_targets = [(i, post, types[i] if i < len(types) else "投稿") for i, post in enumerate(posts)]
    if fallback_post:
        review_targets.append(("fallback", fallback_post, "事例(予備)"))

    revised_count = 0
    skipped_count = 0

    for idx, post, post_type in review_targets:
        label = f"投稿{idx + 1}" if isinstance(idx, int) else "フォールバック投稿"
        print(f"[{label} / {post_type}] レビュー中...")

        issues = check_post(post)

        if not issues:
            print(f"  ✅ 問題なし\n")
            continue

        print(f"  ⚠️  問題検出:")
        for issue in issues:
            print(f"     - {issue}")

        if not client:
            print(f"  ANTHROPIC_API_KEY 未設定 → 修正不可 → この投稿をスキップ\n")
            if isinstance(idx, int):
                review_failed[idx] = True
            else:
                fallback_failed = True
            skipped_count += 1
            continue

        print(f"  🔧 Claude APIで修正中...")
        try:
            revised = revise_post(post, issues, source_context, client)

            remaining = check_post(revised)
            if remaining:
                print(f"  ❌ 修正後も問題が残るため投稿をスキップ: {remaining}")
                if isinstance(idx, int):
                    review_failed[idx] = True
                else:
                    fallback_failed = True
                skipped_count += 1
            else:
                print(f"  ✅ 修正完了")
                if isinstance(idx, int):
                    posts[idx] = revised
                else:
                    fallback_post = revised
                revised_count += 1

        except Exception as e:
            print(f"  ❌ 修正API失敗({e}) → この投稿をスキップ")
            if isinstance(idx, int):
                review_failed[idx] = True
            else:
                fallback_failed = True
            skipped_count += 1

        print()

    # 結果を scheduled JSON に書き戻す
    data["posts"] = posts
    data["review_failed"] = review_failed
    if fallback_post is not None:
        data["fallback_post"] = fallback_post
    if fallback_failed:
        data["fallback_review_failed"] = True

    with open(scheduled_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"レビュー結果を保存: {scheduled_path}")
    print(f"\n=== レビュー完了: {revised_count}件修正 / {skipped_count}件スキップ ===")

    if skipped_count > 0:
        skipped_labels = [
            f"投稿{i+1}" for i, failed in enumerate(review_failed) if failed
        ]
        if fallback_failed:
            skipped_labels.append("フォールバック投稿")
        print(f"スキップ対象: {', '.join(skipped_labels)}")


if __name__ == "__main__":
    main()
