#!/bin/bash
set -e # Exit on error

echo "========================================="
echo "Starting scaling effects evaluation"
echo "========================================="

python eval/rat/eval_rat.py --language python --use-eval --num-turn 15 --weave-project rat-scaling

sleep 20

python eval/rat/eval_rat.py --language python --use-eval --num-turn 20 --weave-project rat-scaling

sleep 20

python eval/rat/eval_rat.py --language python --use-eval --num-turn 25 --weave-project rat-scaling

sleep 20

python eval/rat/eval_rat.py --language python --use-eval --num-turn 30 --weave-project rat-scaling
