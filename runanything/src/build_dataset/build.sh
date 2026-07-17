#!/bin/bash
set -e # Exit on error

echo "========================================="
echo "Starting dataset build and publish"
echo "========================================="

# Python repositories
echo -e "\n[1/4] Fetching Python repositories..."
python -m build_dataset.fetch_python_repos --per-page 100 --count 500 --output-dir datasets/python

# Java repositories
echo -e "\n[2/4] Fetching Java repositories..."
python -m build_dataset.fetch_java_repos --per-page 100 --count 500 --build-tool any --output-dir datasets/java

# Rust repositories
echo -e "\n[3/4] Fetching Rust repositories..."
python -m build_dataset.fetch_rust_repos --per-page 100 --count 500 --output-dir datasets/rust

# JavaScript/TypeScript repositories
echo -e "\n[4/4] Fetching Node.js repositories..."
python -m build_dataset.fetch_nodejs_repos --per-page 100 --count 500 --output-dir datasets/nodejs

echo -e "\n========================================="
echo "Publishing datasets to Weave"
echo "========================================="

# Publish Python datasets
echo -e "\n[Python] Publishing all repos..."
python datasets/publish_datasets.py -f datasets/python/python_repos_all.json -n python-repos-all -p repo-datasets

echo -e "\n[Python] Publishing small repos..."
python datasets/publish_datasets.py -f datasets/python/python_repos_small.json -n python-repos-small -p repo-datasets

echo -e "\n[Python] Publishing medium repos..."
python datasets/publish_datasets.py -f datasets/python/python_repos_medium.json -n python-repos-medium -p repo-datasets

echo -e "\n[Python] Publishing large repos..."
python datasets/publish_datasets.py -f datasets/python/python_repos_large.json -n python-repos-large -p repo-datasets

# Publish Java datasets
echo -e "\n[Java] Publishing all repos..."
python datasets/publish_datasets.py -f datasets/java/java_repos_all.json -n java-repos-all -p repo-datasets

echo -e "\n[Java] Publishing small repos..."
python datasets/publish_datasets.py -f datasets/java/java_repos_small.json -n java-repos-small -p repo-datasets

echo -e "\n[Java] Publishing medium repos..."
python datasets/publish_datasets.py -f datasets/java/java_repos_medium.json -n java-repos-medium -p repo-datasets

echo -e "\n[Java] Publishing large repos..."
python datasets/publish_datasets.py -f datasets/java/java_repos_large.json -n java-repos-large -p repo-datasets

# Publish Rust datasets
echo -e "\n[Rust] Publishing all repos..."
python datasets/publish_datasets.py -f datasets/rust/rust_repos_all.json -n rust-repos-all -p repo-datasets

echo -e "\n[Rust] Publishing small repos..."
python datasets/publish_datasets.py -f datasets/rust/rust_repos_small.json -n rust-repos-small -p repo-datasets

echo -e "\n[Rust] Publishing medium repos..."
python datasets/publish_datasets.py -f datasets/rust/rust_repos_medium.json -n rust-repos-medium -p repo-datasets

echo -e "\n[Rust] Publishing large repos..."
python datasets/publish_datasets.py -f datasets/rust/rust_repos_large.json -n rust-repos-large -p repo-datasets

# Publish Node.js datasets
echo -e "\n[Node.js] Publishing all repos..."
python datasets/publish_datasets.py -f datasets/nodejs/nodejs_repos_all.json -n nodejs-repos-all -p repo-datasets

echo -e "\n[Node.js] Publishing small repos..."
python datasets/publish_datasets.py -f datasets/nodejs/nodejs_repos_small.json -n nodejs-repos-small -p repo-datasets

echo -e "\n[Node.js] Publishing medium repos..."
python datasets/publish_datasets.py -f datasets/nodejs/nodejs_repos_medium.json -n nodejs-repos-medium -p repo-datasets

echo -e "\n[Node.js] Publishing large repos..."
python datasets/publish_datasets.py -f datasets/nodejs/nodejs_repos_large.json -n nodejs-repos-large -p repo-datasets

echo -e "\n========================================="
echo "✓ All datasets built and published to Weave!"
echo "========================================="
echo -e "\nPublished datasets to Weave:"
echo "  - Python: python-repos-{all,small,medium,large}"
echo "  - Java:   java-repos-{all,small,medium,large}"
echo "  - Rust:   rust-repos-{all,small,medium,large}"
echo "  - Node.js: nodejs-repos-{all,small,medium,large}"
echo -e "\nTotal: 16 datasets in project 'repo-datasets'"

echo -e "\n========================================="
echo "Uploading datasets to Hugging Face Hub"
echo "========================================="

# Upload to Hugging Face
echo -e "\nUploading to <omitted> (private)..."
python datasets/publish_to_huggingface.py \
    --dataset-name <omitted>/RAT-bench \
    --data-dir datasets \
    --private

echo -e "\n========================================="
echo "✓ All datasets built and published!"
echo "========================================="
echo -e "\nPublished to:"
echo "  - Weave: 16 datasets in project 'repo-datasets'"
echo "  - Hugging Face: <ommitted> (16 configurations)"
echo -e "\nHugging Face URL: https://huggingface.co/datasets/<omitteddd>/RAT-bench"
