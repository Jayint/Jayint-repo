#!/bin/bash
# pipreqs 评估脚本 - 使用评估子集
# 用法: ./scripts/eval_pipreqs.sh [--language LANG] [--limit N] [--use-eval]

python eval/pipreqs/eval_pipreqs.py --language python --use-eval
