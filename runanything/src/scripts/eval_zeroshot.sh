#!/bin/bash
# ZeroShot 评估脚本 - 使用评估子集
# 用法: ./scripts/eval_zeroshot.sh [--language LANG] [--limit N] [--use-eval]

python eval/zeroshot/eval_zeroshot.py --language python --use-eval
