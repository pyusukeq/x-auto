#!/usr/bin/env python3
"""
投稿レビュー・修正エージェント
scheduled/{today}.json の投稿を審査し、問題があれば Claude API で自動修正する
修正できない場合は review_failed フラグを立てる（投稿はスキップされる）

検出する問題（ルールベース）:
- 👇/⏬ があるがURLがない
- ハッシュタグ使用
- 文字数超過

検出する問題（Claude審査）:
- 見出し・感情表現が元情報の内容と一致しているか（誇張・ミスリードがないか）
- 動画投稿で日本人に伝わりにくい文化的背景の説明が不足していないか
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

REVISE_SYSTEM_PROMPT = """あなたはX投稿の品質管理担当者です。
指摘された問題点を修正した投稿テキストのみを出力してください。
説明・コメント・コードブロックは不要です。"""

CONTENT_CHECK_SYSTEM_PROMPT = """あなたはX投稿の品質管理担当者です。
投稿の正確性・誠実さ・日本語読者への伝わりやすさを審査してください。"""


def weighted_len(text: str) -> int:
    return sum(2 if ord(c) > 127 else 1 for c in text)


def check_post(post: str) -> list:
    """ルールベースで投稿の構造的問題を検出する"""
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


def content_quality_check(post: str, source: str, post_type: str, client) -> list:
    """Claudeを使って見出しの正確性・文化的コンテキストを審査する"""
    prompt = (
        f"以下のX投稿と元情報を照合し、品質上の問題を指摘してください。\n\n"
        f"【投稿文】\n{post}\n\n"
        f"【元情報】\n{source}\n\n"
        f"投稿タイプ: {post_type}\n\n"
        f"チェック項目:\n"
        f"1. 冒頭の感情表現・見出しが元情報の内容と合っているか（誇張・ミスリードがないか）\n"
        f"2. 投稿タイプが「動画」の場合、日本人に伝わりにくい文化的背景・英語圏特有の文脈の説明が不足していないか\n"
        f"3. 全体として日本語読者に正確な情報が伝わるか\n\n"
        f"問題がなければ「問題なし」とだけ返してください。"
        f"問題がある場合は箇条書きで具体的に指摘してください。"
    )
    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=400,
        system=CONTENT_CHECK_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}]
    )
    result = message.content[0].text.strip()
    if "問題なし" in result:
        return []
    return [f"[内容審査] {result}"]


def revise_post(post: str, issues: list, source: str, client) -> str:
    """Claude API で問題のある投稿を修正する"""
    issues_text = "\n".join(f"- {issue}" for issue in issues)

    prompt = (
        f"以下のX投稿に問題が見つかりました。修正した投稿テキストのみを出力してください。\n\n"
        f"【元の投稿】\n{post}\n\n"
        f"【問題点】\n{issues_text}\n\n"
        f"【元情報】\n{source}\n\n"
        f"修正ルール:\n"
        f"- 👇/⏬ の後にURLがない場合: 元情報のURLを追加するか、CTAを「試してみて👀」などに変更\n"
        f"- ハッシュタグは削除\n"
        f"- 見出し・感情表現が内容と合っていない場合: 元情報に忠実な表現に修正\n"
        f"- 文化的コンテキストが不足の場合: 日本語読者向けに1〜2行で補足説明を追加\n"
        f"- 投稿の構造・口調はできるだけ維持\n"
        f"- 全角換算350文字以内に収める"
    )

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        system=REVISE_SYSTEM_PROMPT,
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

    # 元情報の準備
    stories_context = "(元記事情報なし)"
    video_sources = []
    if os.path.exists(collected_path):
        with open(collected_path, "r", encoding="utf-8") as f:
            collected = json.load(f)
        stories = collected.get("top_stories", [])[:5]
        stories_context = "\n".join(
            f"- {s['title'][:60]}: {s.get('url') or s.get('external_url', '')}"
            for s in stories
        )
        for v in collected.get("viral_video_tweets", []):
            video_sources.append(
                f"@{v['author']} のツイート: {v['text'][:400]}\nURL: {v['url']}"
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
    video_idx = 0  # 動画投稿のソース割り当て用カウンタ

    for idx, post, post_type in review_targets:
        label = f"投稿{idx + 1}" if isinstance(idx, int) else "フォールバック投稿"
        print(f"[{label} / {post_type}] レビュー中...")

        # 元情報ソースの選択（動画投稿は動画ツイート原文を使用）
        is_video = "動画" in post_type
        if is_video and video_idx < len(video_sources):
            source = video_sources[video_idx]
            video_idx += 1
        else:
            source = stories_context

        # ① ルールベースチェック
        issues = check_post(post)

        # ② Claude による内容品質チェック（API利用可能時のみ）
        if client:
            try:
                content_issues = content_quality_check(post, source, post_type, client)
                issues.extend(content_issues)
            except Exception as e:
                print(f"  ⚠️  内容審査API失敗({e}) → スキップ")

        if not issues:
            print(f"  ✅ 問題なし\n")
            continue

        print(f"  ⚠️  問題検出:")
        for issue in issues:
            print(f"     - {issue[:100]}")

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
            revised = revise_post(post, issues, source, client)

            remaining = check_post(revised)
            if remaining:
                print(f"  ❌ 修正後も構造的問題が残るため投稿をスキップ: {remaining}")
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
        skipped_labels = [f"投稿{i+1}" for i, failed in enumerate(review_failed) if failed]
        if fallback_failed:
            skipped_labels.append("フォールバック投稿")
        print(f"スキップ対象: {', '.join(skipped_labels)}")


if __name__ == "__main__":
    main()
