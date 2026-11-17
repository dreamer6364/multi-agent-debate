"""评测体系（ISS-014 A-1）：golden set + LLM-as-judge 三维评分。

包布局：
- dataset.py   黄金辩题集（golden_set.jsonl）加载与校验
- judge.py     LLM-as-judge：三维评分 prompt 构建、JSON 解析（围栏剥离+重试+降级）
- run_eval.py  CLI：跑比赛 → 评分 → 报告落盘（--label 支持 prompt 改动前后跑分对比）

使用（在 backend/ 目录下）：
    python -m eval.run_eval --dry-run                    # 只校验资产，不耗 token
    python -m eval.run_eval --topics g01 --format lite --label smoke
"""
