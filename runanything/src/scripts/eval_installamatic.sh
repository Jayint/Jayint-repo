#!/bin/bash
# Installamatic 评估脚本 - 使用评估子集
# 用法: ./scripts/eval_installamatic.sh [--language LANG] [--limit N] [--use-eval]

python eval/installamatic/eval_installamatic.py --language python --use-eval
