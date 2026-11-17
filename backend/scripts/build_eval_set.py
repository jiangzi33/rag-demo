"""
构建 RAG 检索评测集（在 backend 目录下执行，需要 MySQL、Ollama、Milvus 和大模型接口可用）：

    python -m scripts.build_eval_set generate -n 100                 # 随机抽 100 个片段让大模型出题，写入 eval/retrieval_eval.jsonl
    python -m scripts.build_eval_set generate -n 50 --kb-ids 1 2 --append
    python -m scripts.build_eval_set judge                           # 用大模型判定各检索配置的候选片段，补全相关片段
    python -m scripts.build_eval_set judge --pool-k 20 --force       # 已判定过的问题也重新判定

judge 后建议人工抽查评测集；之后用 python -m scripts.eval_retrieval 评估。说明见 app/services/retrieval_eval_service.py。
"""
import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import BACKEND_DIR  # noqa: E402
from app.db.session import engine  # noqa: E402
from app.rag.llm import ChatError, get_chat_client  # noqa: E402
from app.services import retrieval_eval_service as ev  # noqa: E402

DEFAULT_DATASET = BACKEND_DIR / "eval" / "retrieval_eval.jsonl"


def generate(args) -> int:
    path = Path(args.dataset)
    existing = ev.load_dataset(path) if args.append and path.exists() else []
    if path.exists() and not args.append:
        print(f"{path} 已存在：追加请加 --append，或换一个 --dataset", file=sys.stderr)
        return 1
    with engine.connect() as conn:
        cases = ev.generate_cases(conn, get_chat_client(), args.n, args.kb_ids, args.min_chars, args.seed)
    known = {c.id for c in existing}
    new = [c for c in cases if c.id not in known]  # 同一个片段只出一道题
    ev.save_dataset(path, existing + new)
    print(f"新增 {len(new)} 个问题（共 {len(existing) + len(new)} 个）→ {path}")
    print("下一步：python -m scripts.build_eval_set judge 补全相关片段")
    return 0


def judge(args) -> int:
    path = Path(args.dataset)
    cases = ev.load_dataset(path)
    client = get_chat_client()
    todo = [c for c in cases if args.force or not c.judged]
    added = failed = 0
    with engine.connect() as conn:
        user = ev.eval_user(conn, args.user)
        for i, case in enumerate(todo, 1):
            try:
                n = ev.judge_case(conn, user, client, case, args.pool_k)
            except (ChatError, ValueError) as e:
                failed += 1
                print(f"[{i}/{len(todo)}] {case.id} 判定失败：{e}", file=sys.stderr)
                continue
            added += n
            print(f"[{i}/{len(todo)}] {case.id} +{n} 个相关片段（共 {len(case.relevant_chunk_ids)}）{case.question}")
            if i % 10 == 0:
                ev.save_dataset(path, cases)  # 中途中断时保留已判定的结果
    ev.save_dataset(path, cases)
    print(f"完成：判定 {len(todo) - failed} 个问题，新增 {added} 个相关片段，{failed} 个失败 → {path}")
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="构建 RAG 检索评测集")
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET), help=f"评测集路径，默认 {DEFAULT_DATASET}")
    sub = parser.add_subparsers(dest="command", required=True)

    g = sub.add_parser("generate", help="随机抽取片段，让大模型出题")
    g.add_argument("-n", type=int, default=100, help="抽取的片段数")
    g.add_argument("--kb-ids", type=int, nargs="*", help="只从这些知识库抽取")
    g.add_argument("--min-chars", type=int, default=80, help="跳过正文少于这么多字的片段")
    g.add_argument("--seed", type=int, help="随机种子，固定后可复现抽样")
    g.add_argument("--append", action="store_true", help="追加到已有评测集")
    g.set_defaults(func=generate)

    j = sub.add_parser("judge", help="用大模型判定候选片段，补全相关片段")
    j.add_argument("--pool-k", type=int, default=10, help="每种检索方式取前多少条作为候选，应不小于评估的最大 k")
    j.add_argument("--user", help="以哪个用户检索，默认第一个系统管理员")
    j.add_argument("--force", action="store_true", help="已判定过的问题也重新判定")
    j.set_defaults(func=judge)

    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
