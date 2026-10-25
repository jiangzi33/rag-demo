"""
富文本正文的 HTML 白名单过滤（服务端兜底，前端展示时还会用 DOMPurify 再过滤一次）。

只保留编辑器（wangEditor）会产生的标签、属性和样式：
字体 / 字号 / 颜色 / 背景色 / 对齐 / 缩进 / 行高、图片、表格、待办、代码块、链接等；
<script>、事件属性（onerror 等）、javascript: 链接、未列出的样式一律去掉。
"""
import re

import nh3

_TAGS = {
    "p", "br", "span", "strong", "b", "em", "i", "u", "s", "del", "sub", "sup", "mark",
    "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "pre", "code", "hr", "div",
    "ul", "ol", "li", "a", "img", "input",
    "table", "colgroup", "col", "thead", "tbody", "tr", "th", "td",
}

_ATTRIBUTES = {
    "*": {"style"},
    "a": {"href", "target"},
    "img": {"src", "alt", "data-href", "width", "height"},
    "input": {"disabled", "checked"},
    "code": {"class"},
    "th": {"colspan", "rowspan", "width"},
    "td": {"colspan", "rowspan", "width"},
    "col": {"width"},
}

# 只允许特定取值的属性：待办列表的复选框、编辑器的块类型标记
_TAG_ATTRIBUTE_VALUES = {
    "input": {"type": {"checkbox"}},
    "div": {"data-w-e-type": {"todo"}},
}

_STYLE_PROPERTIES = {
    "color", "background-color", "font-size", "font-family", "font-weight", "font-style",
    "text-decoration", "text-align", "text-indent", "line-height", "vertical-align",
    "width", "height", "max-width", "border", "border-collapse",
}


# 样式值中禁止出现的内容：外部资源、旧 IE 表达式、转义序列、注释、尖括号
_UNSAFE_STYLE_VALUE = re.compile(r"url\s*\(|expression|\\|/\*|[<>]", re.IGNORECASE)


def _clean_style(style: str) -> str | None:
    """只保留白名单中的样式属性，并且原样保留取值的书写格式。
    不使用 nh3 自带的 filter_style_properties：它会把 `KaiTi, "Kaiti SC"` 改写成 `KaiTi , "Kaiti SC"`，
    编辑器再次加载时按原值匹配字体列表，格式一变字体就丢了。"""
    kept = []
    for decl in style.split(";"):
        name, sep, value = decl.partition(":")
        name, value = name.strip().lower(), value.strip()
        if sep and value and name in _STYLE_PROPERTIES and not _UNSAFE_STYLE_VALUE.search(value):
            kept.append(f"{name}: {value};")
    return " ".join(kept) or None


def _attribute_filter(_tag: str, attr: str, value: str) -> str | None:
    return _clean_style(value) if attr == "style" else value


def clean_html(html: str) -> str:
    return nh3.clean(
        html,
        tags=_TAGS,
        attributes=_ATTRIBUTES,
        tag_attribute_values=_TAG_ATTRIBUTE_VALUES,
        attribute_filter=_attribute_filter,
        url_schemes={"http", "https", "mailto"},
    )
