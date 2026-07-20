#!/bin/bash
# SWE-agent 评估脚本 - 使用评估子集
# 用法: ./scripts/eval_sweagent.sh [--language LANG] [--limit N] [--use-eval]

python eval/sweagent/eval_sweagent.py --language python --use-eval
