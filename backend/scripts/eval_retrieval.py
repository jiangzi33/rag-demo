"""
评估 RAG 检索效果：对比不同检索配置在评测集上的召回率、精确度等指标（在 backend 目录下执行）：

    python -m scripts.eval_retrieval                          # 默认对比 vector / hybrid / hybrid+rewrite，k = 1 3 5 10
    python -m scripts.eval_retrieval --configs vector hybrid  # 不需要大模型接口
    python -m scripts.eval_retrieval --ks 3 5 --main-k 5 --tags keyword paraphrase --min-score 0.4

结果打印到终端，并在 eval/reports/ 下保存 Markdown 摘要和完整 JSON（含每个问题的检索结果，便于分析 bad case）。
调参（RAG_RRF_K、RAG_KEYWORD_WEIGHT、RAG_HYBRID_CANDIDATES、切分大小等）时用环境变量覆盖后重新运行即可对比，例如：
    RAG_KEYWORD_WEIGHT=1.5 python -m scripts.eval_retrieval --configs hybrid --name kw1.5
评测集的格式和构建方法见 app/services/retrieval_eval_service.py、scripts/build_eval_set.py。
"""
import argparse
import json
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import BACKEND_DIR, settings  # noqa: E402
from app.db.session import engine  # noqa: E402
from app.rag.llm import get_chat_client  # noqa: E402
from app.services import retrieval_eval_service as ev  # noqa: E402

DEFAULT_DATASET = BACKEND_DIR / "eval" / "retrieval_eval.jsonl"
REPORT_DIR = BACKEND_DIR / "eval" / "reports"

PRESETS = {
    "vector": ev.EvalConfig("vector", "vector"),
    "hybrid": ev.EvalConfig("hybrid", "hybrid"),
    "vector+rewrite": ev.EvalConfig("vector+rewrite", "vector", rewrite=True),
    "hybrid+rewrite": ev.EvalConfig("hybrid+rewrite", "hybrid", rewrite=True),
}


def main() -> int:
    parser = argparse.ArgumentParser(description="评估 RAG 检索的召回率和精确度")
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET), help=f"评测集路径，默认 {DEFAULT_DATASET}")
    parser.add_argument("--configs", nargs="+", choices=list(PRESETS), default=["vector", "hybrid", "hybrid+rewrite"])
    parser.add_argument("--ks", type=int, nargs="+", default=[1, 3, 5, 10], help=f"评估的 top k，最大 {settings.RAG_TOP_K_MAX}")
    parser.add_argument("--main-k", type=int, help="按问题类型统计、列出漏召回问题时用的 k，默认 QA_TOP_K（问答实际使用的条数）")
    parser.add_argument("--min-score", type=float, help="余弦相似度下限，不填使用 RAG_MIN_SCORE")
    parser.add_argument("--tags", nargs="*", help="只评估带这些类型的问题")
    parser.add_argument("--limit", type=int, help="只评估前 n 个问题（快速试跑）")
    parser.add_argument("--user", help="以哪个用户检索，默认第一个系统管理员")
    parser.add_argument("--name", default="", help="报告文件名后缀，用于区分不同参数的运行")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    cases = ev.load_dataset(args.dataset)
    if args.tags:
        cases = [c for c in cases if set(c.tags) & set(args.tags)]
    cases = cases[: args.limit] if args.limit else cases
    if not cases:
        print("评测集中没有符合条件的问题", file=sys.stderr)
        return 1
    main_k = args.main_k or settings.QA_TOP_K
    ks = sorted(set(args.ks) | {main_k})
    configs = [ev.EvalConfig(**{**vars(PRESETS[name]), "min_score": args.min_score}) for name in args.configs]
    client = get_chat_client() if any(c.rewrite for c in configs) else None

    with engine.connect() as conn:
        stale = ev.stale_chunk_ids(conn, cases)
        if stale:
            bad = [c.id for c in cases if stale & set(c.relevant_chunk_ids)]
            print(f"警告：{len(bad)} 个问题标注的片段已不存在（文档编辑或重新切分后 id 会变），这些问题的召回会偏低："
                  f"{', '.join(bad[:20])}{' …' if len(bad) > 20 else ''}", file=sys.stderr)
        user = ev.eval_user(conn, args.user)
        print(f"评估 {len(cases)} 个问题，配置 {', '.join(c.name for c in configs)}，以用户 {user.username} 检索 …")
        report = ev.run(conn, user, cases, configs, ks, client=client)

    report["dataset"] = str(args.dataset)
    markdown = ev.to_markdown(report, main_k)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    stem = time.strftime("%Y%m%d-%H%M%S") + (f"-{args.name}" if args.name else "")
    (REPORT_DIR / f"{stem}.md").write_text(markdown, encoding="utf-8")
    (REPORT_DIR / f"{stem}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(markdown)
    print(f"报告已保存：{REPORT_DIR / stem}.md / .json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
