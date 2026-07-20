#!/bin/bash
# Repo2Run 评估脚本 - 使用评估子集
# 用法: ./scripts/eval_repo2run.sh [--language LANG] [--limit N] [--use-eval]

python eval/repo2run/eval_repo2run.py --language python --use-eval
