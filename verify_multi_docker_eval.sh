#!/bin/bash
# Multi-Docker-Eval 适配验证脚本

set -e

echo "=========================================="
echo "Multi-Docker-Eval 适配验证"
echo "=========================================="

# 1. 检查环境
echo ""
echo "[1/5] 检查环境依赖..."

if ! command -v docker &> /dev/null; then
    echo "❌ Docker 未安装或未启动"
    exit 1
fi
echo "✓ Docker 已就绪"

if ! docker info &> /dev/null; then
    echo "❌ Docker daemon 未运行"
    exit 1
fi
echo "✓ Docker daemon 运行中"

if [ ! -f ".env" ]; then
    echo "❌ .env 文件不存在，请从 .env.example 复制并配置"
    exit 1
fi
echo "✓ .env 文件存在"

# 检查 Python 依赖
if ! python -c "import docker, openai" &> /dev/null; then
    echo "❌ Python 依赖缺失，请运行: uv pip install -r requirements.txt"
    exit 1
fi
echo "✓ Python 依赖已安装"

# 2. 运行测试实例
echo ""
echo "[2/5] 运行测试实例..."
echo "这将使用 test_dataset.jsonl 中的示例"

if [ ! -f "test_dataset.jsonl" ]; then
    echo "❌ test_dataset.jsonl 不存在"
    exit 1
fi

python multi_docker_eval_adapter.py \
    test_dataset.jsonl \
    --output-dir ./test_output \
    --model gpt-4o \
    --max-steps 10 \
    --limit 1

# 3. 验证输出格式
echo ""
echo "[3/5] 验证输出格式..."

if [ ! -f "./test_output/docker_res.json" ]; then
    echo "❌ docker_res.json 未生成"
    exit 1
fi
echo "✓ docker_res.json 已生成"

# 检查 JSON 格式
if ! python -c "import json; json.load(open('./test_output/docker_res.json'))" &> /dev/null; then
    echo "❌ docker_res.json 格式无效"
    exit 1
fi
echo "✓ JSON 格式正确"

# 4. 检查必需字段
echo ""
echo "[4/5] 检查输出字段..."

python << EOF
import json

with open('./test_output/docker_res.json', 'r') as f:
    results = json.load(f)

if not results:
    print("❌ 结果列表为空")
    exit(1)

result = results[0]
required_fields = ['instance_id', 'dockerfile', 'test_script', 'build_success']

for field in required_fields:
    if field not in result:
        print(f"❌ 缺少必需字段: {field}")
        exit(1)
    print(f"✓ 字段存在: {field}")

print(f"\n输出摘要:")
print(f"  Instance ID: {result['instance_id']}")
print(f"  Build Success: {result['build_success']}")
print(f"  Dockerfile 长度: {len(result.get('dockerfile', '') or '')} 字符")
print(f"  Test Script 长度: {len(result.get('test_script', '') or '')} 字符")
EOF

# 5. 总结
echo ""
echo "[5/5] 验证完成"
echo "=========================================="
echo "✅ Multi-Docker-Eval 适配验证成功！"
echo "=========================================="
echo ""
echo "下一步："
echo "1. 下载完整数据集:"
echo "   git clone https://huggingface.co/datasets/litble/Multi-Docker-Eval"
echo ""
echo "2. 运行完整评估:"
echo "   python multi_docker_eval_adapter.py Multi-Docker-Eval/task.jsonl"
echo ""
echo "3. 提交到评估框架:"
echo "   git clone https://github.com/Z2sJ4t/Multi-Docker-Eval.git"
echo "   cd Multi-Docker-Eval"
echo "   python3 ./evaluation/main.py \\"
echo "       base.dataset='path/to/task.jsonl' \\"
echo "       base.docker_res='path/to/docker_res.json' \\"
echo "       base.run_id='your_agent_name'"
echo ""
