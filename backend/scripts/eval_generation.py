"""
评估 RAG 生成效果：检索到片段后，大模型的回答在忠实度、相关性、完整性、正确性上的表现（在 backend 目录下执行）：

    python -m scripts.build_eval_set reference                    # 先为评测集生成参考答案和要点（完整性、正确性需要）
    python -m scripts.eval_generation                             # 默认对比 gold（只评估生成）和 hybrid+rewrite（端到端）
    python -m scripts.eval_generation --configs hybrid --limit 20 --tags keyword
    EVAL_JUDGE_MODEL=gpt-4.1 python -m scripts.eval_generation --model gpt-4.1-nano --name nano   # 换生成模型对比

结果打印到终端，并在 eval/reports/ 下保存 Markdown 摘要和完整 JSON（含每个问题的回答、陈述判定、遗漏要点）。
指标说明见 app/services/generation_eval_service.py。
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
from app.services import generation_eval_service as gen  # noqa: E402
from app.services import retrieval_eval_service as ev  # noqa: E402

DEFAULT_DATASET = BACKEND_DIR / "eval" / "retrieval_eval.jsonl"
REPORT_DIR = BACKEND_DIR / "eval" / "reports"

PRESETS = {
    "gold": gen.GenEvalConfig("gold", context="gold"),
    "vector": gen.GenEvalConfig("vector", mode="vector"),
    "hybrid": gen.GenEvalConfig("hybrid", mode="hybrid"),
    "hybrid+rewrite": gen.GenEvalConfig("hybrid+rewrite", mode="hybrid", rewrite=True),
}


def main() -> int:
    parser = argparse.ArgumentParser(description="评估 RAG 回答的忠实度、相关性、完整性、正确性")
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET), help=f"评测集路径，默认 {DEFAULT_DATASET}")
    parser.add_argument("--configs", nargs="+", choices=list(PRESETS), default=["gold", "hybrid+rewrite"])
    parser.add_argument("--top-k", type=int, help="交给大模型的片段数，默认 QA_TOP_K")
    parser.add_argument("--model", help="生成回答的模型，默认 OPENAI_MODEL（裁判模型用 EVAL_JUDGE_MODEL 设置）")
    parser.add_argument("--tags", nargs="*", help="只评估带这些类型的问题")
    parser.add_argument("--limit", type=int, help="只评估前 n 个问题（快速试跑）")
    parser.add_argument("--user", help="以哪个用户检索，默认第一个系统管理员")
    parser.add_argument("--name", default="", help="报告文件名后缀，用于区分不同参数的运行")
    parser.add_argument("-v", "--verbose", action="store_true", help="打印每个问题的得分")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    if args.verbose:
        logging.getLogger(gen.__name__).setLevel(logging.INFO)

    cases = ev.load_dataset(args.dataset)
    if args.tags:
        cases = [c for c in cases if set(c.tags) & set(args.tags)]
    cases = cases[: args.limit] if args.limit else cases
    if not cases:
        print("评测集中没有符合条件的问题", file=sys.stderr)
        return 1
    if not any(c.reference_answer for c in cases):
        print("警告：评测集中没有参考答案，只评估忠实度和相关性；"
              "先运行 python -m scripts.build_eval_set reference 生成", file=sys.stderr)
    configs = [gen.GenEvalConfig(**{**vars(PRESETS[name]), "top_k": args.top_k, "model": args.model}) for name in args.configs]
    client = get_chat_client()

    with engine.connect() as conn:
        stale = ev.stale_chunk_ids(conn, cases)
        if stale:
            bad = [c.id for c in cases if stale & set(c.relevant_chunk_ids)]
            print(f"警告：{len(bad)} 个问题标注的片段已不存在，gold 上下文和上下文召回会偏低："
                  f"{', '.join(bad[:20])}{' …' if len(bad) > 20 else ''}", file=sys.stderr)
        user = ev.eval_user(conn, args.user)
        print(f"评估 {len(cases)} 个问题，配置 {', '.join(c.name for c in configs)}，"
              f"生成模型 {args.model or client.model}，裁判模型 {settings.EVAL_JUDGE_MODEL or client.model} …")
        report = gen.run(conn, user, cases, configs, client=client)

    report["dataset"] = str(args.dataset)
    markdown = gen.to_markdown(report)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    stem = time.strftime("%Y%m%d-%H%M%S") + "-generation" + (f"-{args.name}" if args.name else "")
    (REPORT_DIR / f"{stem}.md").write_text(markdown, encoding="utf-8")
    (REPORT_DIR / f"{stem}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(markdown)
    print(f"报告已保存：{REPORT_DIR / stem}.md / .json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
