"""
WebEngine memory test — simulate an AI chat panel and measure memory usage.

Run: python scripts/webengine_memory_test.py

Close the window to see the memory report.
"""

import sys
import os
import time

# ── Memory measurement ──────────────────────────────────────────────

def get_memory_mb():
    """Return current process RSS in MB. Uses psutil if available."""
    try:
        import psutil
        p = psutil.Process(os.getpid())
        return p.memory_info().rss / (1024 * 1024)
    except ImportError:
        return 0  # psutil not available — use task manager


def format_mem(mb: float) -> str:
    if mb == 0:
        return "(psutil not installed — check Task Manager)"
    return f"{mb:.1f} MB"


# ── HTML template for a chat UI ─────────────────────────────────────

CHAT_HTML = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    font-family: -apple-system, "Microsoft YaHei", sans-serif;
    font-size: 14px;
    background: #1a1a2e;
    color: #e0e0e0;
    padding: 16px;
    line-height: 1.6;
  }
  #chat-container {
    max-width: 600px;
    margin: 0 auto;
  }
  .bubble {
    margin-bottom: 12px;
    padding: 10px 14px;
    border-radius: 12px;
    max-width: 85%;
    word-wrap: break-word;
  }
  .user {
    background: #0d6efd;
    color: #fff;
    margin-left: auto;
    text-align: right;
  }
  .assistant {
    background: #2d2d44;
    color: #ddd;
    margin-right: auto;
  }
  .system {
    text-align: center;
    color: #888;
    font-size: 12px;
    margin: 8px 0;
  }
  .assistant code {
    background: #1a1a2e;
    padding: 2px 6px;
    border-radius: 4px;
    font-family: "Cascadia Code", "Consolas", monospace;
    font-size: 13px;
  }
  .assistant pre {
    background: #1a1a2e;
    padding: 12px;
    border-radius: 8px;
    overflow-x: auto;
    margin: 8px 0;
  }
  .assistant p { margin: 4px 0; }
  .assistant ul, .assistant ol { margin: 4px 0; padding-left: 20px; }
  .assistant li { margin: 2px 0; }
</style>
</head>
<body>
<div id="chat-container">
  <div class="system">=== AI Assistant Demo (WebEngine) ===</div>
  <div class="bubble user">你好，能帮我翻译这段文字吗？</div>
  <div class="bubble assistant">
    <p>当然可以！请把需要翻译的文字发给我，我会帮你翻译。</p>
    <p>我支持以下功能：</p>
    <ul>
      <li>多语言翻译（中日英韩等）</li>
      <li>保持原文风格和语气</li>
      <li>专业术语一致性</li>
    </ul>
  </div>
  <div class="bubble user">翻译成中文：The quick brown fox jumps over the lazy dog.</div>
  <div class="bubble assistant">
    <p>这句话可以翻译为：</p>
    <p><strong>那只敏捷的棕色狐狸从那只懒狗身上跳了过去。</strong></p>
    <p>这是一个经典的英语全字母句（pangram），包含了26个英文字母。</p>
  </div>
  <div class="bubble user">解释一下Python的装饰器是什么。</div>
  <div id="streaming-target" class="bubble assistant">
    <p><strong>Python 装饰器</strong>是一种设计模式，允许在不修改原函数代码的情况下，给函数添加新的功能。</p>
    <p>装饰器本质上是一个<strong>接受函数作为参数并返回新函数</strong>的高阶函数。</p>
    <p>常见用途：</p>
    <ul>
      <li><strong>日志记录</strong> — 自动记录函数调用</li>
      <li><strong>权限检查</strong> — 验证用户是否有权执行</li>
      <li><strong>缓存</strong> — 缓存函数返回值</li>
      <li><strong>计时</strong> — 测量函数执行时间</li>
    </ul>
    <p>装饰器让代码更<em>简洁</em>、更<em>可维护</em>，是Python中最优雅的特性之一。</p>
  </div>
  <div class="system">=== 共计 5 条消息 ===</div>
</div>
</body>
</html>"""


def main():
    from qtpy.QtWidgets import QApplication
    from qtpy.QtWebEngineWidgets import QWebEngineView
    from qtpy.QtCore import QTimer, Qt

    app = QApplication(sys.argv)

    # Measure baseline memory (before WebEngine)
    baseline_mb = get_memory_mb()
    print(f"Baseline memory (QApplication only): {format_mem(baseline_mb)}")

    view = QWebEngineView()
    view.setWindowTitle("WebEngine Chat Demo — Close window to finish")
    view.resize(520, 700)

    # Show immediately so we can measure
    view.show()

    # Load HTML after a tick so the WebEngine process has time to spin up
    def on_load_finished(ok):
        after_load_mb = get_memory_mb()
        delta = after_load_mb - baseline_mb
        print(f"Memory after page loaded:       {format_mem(after_load_mb)}  (Δ +{delta:.1f} MB)")

        # --- Simulate streaming: append text to the streaming bubble ---
        streaming_chunks = [
            "\n    <p>下面再补充一个实际例子：</p>\n",
            '    <pre><code>def timer(func):\n'
            '    import time\n'
            '    def wrapper(*args, **kwargs):\n'
            '        start = time.time()\n'
            '        result = func(*args, **kwargs)\n'
            '        print(f"{func.__name__} took {time.time()-start:.2f}s")\n'
            '        return result\n'
            '    return wrapper</code></pre>\n',
            "    <p>使用时只需要在函数定义前加上 <code>@timer</code> 即可自动计时。</p>\n",
        ]

        def append_chunk(i=0):
            if i >= len(streaming_chunks):
                # Done — final memory reading
                final_mb = get_memory_mb()
                total_delta = final_mb - baseline_mb
                print(f"Memory after 3 streaming chunks: {format_mem(final_mb)}  (Δ +{total_delta:.1f} MB)")
                print()
                print("=== Keep the window open to observe steady-state memory in Task Manager ===")
                print("Close the WebEngine window to exit and see final report.")
                return

            chunk_html = streaming_chunks[i]
            view.page().runJavaScript(
                f"document.getElementById('streaming-target').innerHTML += "
                f"`{chunk_html}`;"
            )
            QTimer.singleShot(800, lambda: append_chunk(i + 1))

        QTimer.singleShot(500, lambda: append_chunk(0))

    view.loadFinished.connect(on_load_finished)
    view.setHtml(CHAT_HTML)

    app.exec()

    # After window closes
    final_mb = get_memory_mb()
    print(f"\nMemory after window closed:  {format_mem(final_mb)}")
    print(f"Total delta from baseline:   {final_mb - baseline_mb:+.1f} MB")
    print("(WebEngine may not fully release memory until process exits)")


if __name__ == "__main__":
    main()
