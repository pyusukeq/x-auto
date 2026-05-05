"""
コピーボタン付き下書きHTML生成ヘルパー
docs/daily.html と docs/rewrite.html を生成するために使用する
"""

import html as _html
import os
from datetime import datetime, timezone, timedelta

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS_DIR = os.path.join(BASE_DIR, "docs")

_CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: -apple-system, BlinkMacSystemFont, 'Helvetica Neue', sans-serif;
  background: #f0f2f5;
  min-height: 100vh;
  padding: 12px;
  max-width: 640px;
  margin: 0 auto;
}
header { text-align: center; padding: 16px 0 8px; }
header h1 { font-size: 20px; color: #0f1419; }
header p { font-size: 13px; color: #536471; margin-top: 4px; }
.card {
  background: white;
  border-radius: 16px;
  padding: 16px;
  margin: 12px 0;
  box-shadow: 0 1px 4px rgba(0,0,0,0.08);
}
.card-label {
  font-size: 11px;
  font-weight: 700;
  color: #536471;
  text-transform: uppercase;
  letter-spacing: 0.8px;
  margin-bottom: 10px;
}
.warning { font-size: 12px; color: #f4212e; margin-bottom: 8px; }
.post-text {
  white-space: pre-wrap;
  font-size: 15px;
  line-height: 1.75;
  color: #0f1419;
  padding: 12px;
  background: #f7f9f9;
  border-radius: 10px;
  margin-bottom: 12px;
  word-break: break-word;
}
.post-text.failed { border-left: 3px solid #f4212e; }
.copy-btn {
  display: block;
  width: 100%;
  background: #1d9bf0;
  color: white;
  border: none;
  border-radius: 9999px;
  padding: 12px;
  font-size: 16px;
  font-weight: 700;
  cursor: pointer;
  -webkit-tap-highlight-color: transparent;
}
.copy-btn.done { background: #00ba7c; }
.copy-btn:active { opacity: 0.85; }
.source-url {
  font-size: 12px;
  color: #536471;
  background: #f7f9f9;
  padding: 8px 12px;
  border-radius: 8px;
  margin-top: 8px;
  word-break: break-all;
}
"""

_SCRIPT = """
function copy(id, btn) {
  var text = document.getElementById(id).innerText;
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(function() { done(btn); }, function() { fallback(text, btn); });
  } else {
    fallback(text, btn);
  }
}
function fallback(text, btn) {
  var ta = document.createElement('textarea');
  ta.value = text;
  ta.style.cssText = 'position:fixed;top:0;left:0;opacity:0;';
  document.body.appendChild(ta);
  ta.focus(); ta.select(); ta.setSelectionRange(0, 99999);
  try { document.execCommand('copy'); done(btn); }
  catch(e) { alert('コピーできませんでした。長押しで選択してください。'); }
  document.body.removeChild(ta);
}
function done(btn) {
  btn.textContent = 'コピー完了 ✓';
  btn.classList.add('done');
  setTimeout(function() { btn.textContent = 'コピー'; btn.classList.remove('done'); }, 2500);
}
"""


def _page(title: str, subtitle: str, cards_html: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
<title>{_html.escape(title)}</title>
<style>{_CSS}</style>
</head>
<body>
<header>
  <h1>{_html.escape(title)}</h1>
  <p>{_html.escape(subtitle)}</p>
</header>
{cards_html}
<script>{_SCRIPT}</script>
</body>
</html>"""


def _card(post_id: str, label: str, text: str, failed: bool = False, source_url: str = None) -> str:
    warning = '<p class="warning">⚠️ レビュー未通過（投稿スキップ推奨）</p>' if failed else ""
    text_class = "post-text failed" if failed else "post-text"
    url_block = ""
    if source_url:
        url_block = f'<div class="source-url">元ツイートURL（動画引用時に使用）:<br>{_html.escape(source_url)}</div>'
    return (
        f'<div class="card">'
        f'<div class="card-label">{_html.escape(label)}</div>'
        f'{warning}'
        f'<div class="{text_class}" id="{post_id}">{_html.escape(text)}</div>'
        f'<button class="copy-btn" onclick="copy(\'{post_id}\', this)">コピー</button>'
        f'{url_block}'
        f'</div>'
    )


def _jst_now() -> str:
    jst = datetime.now(timezone(timedelta(hours=9)))
    return jst.strftime("%Y-%m-%d %H:%M JST")


def save_daily_html(posts: list, types: list, review_failed: list,
                    fallback_post: str, fallback_failed: bool, date_str: str):
    """毎日の下書きを docs/daily.html に保存する"""
    cards = []
    for i, post in enumerate(posts):
        label = f"投稿{i+1}（{types[i] if i < len(types) else '投稿'}）"
        failed = review_failed[i] if i < len(review_failed) else False
        cards.append(_card(f"p{i}", label, post, failed))

    if fallback_post:
        label = "予備投稿（使用任意）"
        cards.append(_card("pfb", label, fallback_post, fallback_failed))

    html_content = _page(
        title=f"X投稿下書き {date_str}",
        subtitle=f"生成: {_jst_now()}",
        cards_html="\n".join(cards),
    )
    os.makedirs(DOCS_DIR, exist_ok=True)
    path = os.path.join(DOCS_DIR, "daily.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html_content)
    print(f"HTML下書き保存: {path}")


def save_rewrite_html(text: str, original_url: str, date_str: str):
    """リライト下書きを docs/rewrite.html に保存する"""
    video_url = original_url.rstrip("/") + "/video/1"
    card = _card("p0", "リライト投稿", text, source_url=video_url)

    html_content = _page(
        title=f"リライト下書き {date_str}",
        subtitle=f"生成: {_jst_now()} | 元URL: {original_url}",
        cards_html=card,
    )
    os.makedirs(DOCS_DIR, exist_ok=True)
    path = os.path.join(DOCS_DIR, "rewrite.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html_content)
    print(f"リライトHTML保存: {path}")
