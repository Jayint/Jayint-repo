#!/bin/bash
# RAT 评估脚本 - 使用评估子集
# 用法: ./scripts/eval_rat.sh [--language LANG] [--limit N] [--use-eval]

python eval/rat/eval_rat.py --language python --use-eval
