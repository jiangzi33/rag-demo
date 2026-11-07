import pytest

from app.rag import DocumentChunker, html_to_blocks


def test_html_to_blocks():
    html = """
    <h1>安装</h1><p>先&nbsp;下载  <strong>安装包</strong>。<br>再解压。</p>
    <ul><li>Windows</li><li>macOS<ol><li>Intel</li><li>Apple 芯片</li></ol></li></ul>
    <div data-w-e-type="todo"><input type="checkbox" disabled checked>已完成</div>
    <blockquote>注意事项</blockquote>
    <pre><code class="language-python">def f():
    return 1</code></pre>
    <table><tbody><tr><th>参数</th><th>说明</th></tr><tr><td>a|b</td><td><p>第一行</p><p>第二行</p></td></tr></tbody></table>
    <p><img src="/x.png" alt="架构图"></p><script>alert(1)</script>
    """
    blocks = [(b.type, b.text) for b in html_to_blocks(html)]
    assert blocks == [
        ("heading", "安装"),
        ("paragraph", "先 下载 安装包。\n再解压。"),
        ("list_item", "- Windows"),
        ("list_item", "- macOS"),
        ("list_item", "  1. Intel"),
        ("list_item", "  2. Apple 芯片"),
        ("paragraph", "[x] 已完成"),
        ("quote", "> 注意事项"),
        ("code", "```python\ndef f():\n    return 1\n```"),
        ("table", "| 参数 | 说明 |\n| --- | --- |\n| a\\|b | 第一行 第二行 |"),
        ("paragraph", "[图片: 架构图]"),
    ]
    assert html_to_blocks("") == []


def test_heading_path_and_sections():
    html = "<p>前言</p><h1>A</h1><p>a1</p><h2>B</h2><p>b1</p><h2>C</h2><p>c1</p><h1>D</h1><h2>E</h2><p>e1</p>"
    chunks = DocumentChunker(chunk_size=100, chunk_overlap=0).split_html(html, title="手册", doc_id=3, kb_id=7)
    assert [(c.index, c.heading_path, c.content) for c in chunks] == [
        (0, [], "前言"),
        (1, ["A"], "a1"),
        (2, ["A", "B"], "b1"),
        (3, ["A", "C"], "c1"),
        (4, ["D", "E"], "e1"),
    ]
    assert chunks[2].embedding_text == "手册 > A > B\n\nb1"
    assert (chunks[0].doc_id, chunks[0].kb_id) == (3, 7)


def test_long_paragraph_split_with_overlap():
    sentences = [f"这是第{i:02d}句话。" for i in range(40)]  # 每句 8 个字符
    chunks = DocumentChunker(chunk_size=50, chunk_overlap=16).split_html(f"<p>{''.join(sentences)}</p>")
    assert len(chunks) > 1
    assert all(len(c.content) <= 50 for c in chunks)
    # 在句号处切分，相邻块有重叠
    assert all(c.content.endswith("。") for c in chunks)
    for prev, nxt in zip(chunks, chunks[1:]):
        assert nxt.content.startswith(prev.content[-16:])
    # 去掉重叠后能还原原文
    restored = chunks[0].content + "".join(c.content[16:] for c in chunks[1:])
    assert restored == "".join(sentences)


def test_small_blocks_are_merged():
    html = "".join(f"<p>段落{i}</p>" for i in range(5))
    chunks = DocumentChunker(chunk_size=100, chunk_overlap=0).split_html(html)
    assert [c.content for c in chunks] == ["段落0\n段落1\n段落2\n段落3\n段落4"]


def test_long_table_keeps_header():
    rows = "".join(f"<tr><td>key{i}</td><td>value{i}</td></tr>" for i in range(30))
    html = f"<table><tr><th>键</th><th>值</th></tr>{rows}</table>"
    chunks = DocumentChunker(chunk_size=120, chunk_overlap=0).split_html(html)
    assert len(chunks) > 1
    for c in chunks:
        assert len(c.content) <= 120
        assert c.content.startswith("| 键 | 值 |\n| --- | --- |\n")
    assert sum(c.content.count("| key") for c in chunks) == 30


def test_long_code_keeps_fence():
    code = "\n".join(f"print({i})" for i in range(50))
    chunks = DocumentChunker(chunk_size=80, chunk_overlap=0).split_html(f'<pre><code class="language-python">{code}</code></pre>')
    assert len(chunks) > 1
    for c in chunks:
        assert len(c.content) <= 80
        assert c.content.startswith("```python\n") and c.content.endswith("\n```")


def test_hard_cut_without_separators():
    chunks = DocumentChunker(chunk_size=10, chunk_overlap=0).split_html("<p>" + "x" * 35 + "</p>")
    assert [len(c.content) for c in chunks] == [10, 10, 10, 5]


def test_invalid_params():
    with pytest.raises(ValueError):
        DocumentChunker(chunk_size=100, chunk_overlap=100)
