#!/usr/bin/env python3
"""
Upload repository datasets to Hugging Face Hub

This script converts JSON files to JSONL format and uploads them as 
a multi-configuration dataset to Hugging Face Hub.

Usage:
    python datasets/publish_to_huggingface.py \\
        --dataset-name <omitted> \\
        --data-dir datasets \\
        --private

Environment variables:
    HF_TOKEN: Hugging Face API token (required)
"""

import argparse
import json
import os
import random
import sys
from pathlib import Path
from typing import Any, Dict, List

from dotenv import load_dotenv
from huggingface_hub import HfApi, create_repo

from datasets import Dataset, DatasetDict, load_dataset


def load_json_to_list(json_file: Path) -> List[Dict[str, Any]]:
    """Load JSON file and return as list of dictionaries."""
    with open(json_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, list):
        raise ValueError(f"Expected JSON array in {json_file}, got {type(data)}")

    return data


def load_summary(summary_file: Path) -> Dict[str, Any]:
    """Load summary JSON file."""
    if not summary_file.exists():
        return {}

    with open(summary_file, "r", encoding="utf-8") as f:
        return json.load(f)


def create_dataset_card(
    dataset_name: str,
    data_dir: Path,
    languages: List[str],
    stats: Dict[str, Dict[str, Any]],
) -> str:
    """Generate dataset card (README.md) content."""

    card = f"""---
license: apache-2.0
task_categories:
- text-generation
language:
- en
tags:
- code
- docker
- testing
- repositories
- github
- code-generation
size_categories:
- 1K<n<10K
---

# RAT-bench: RunAnyThing Benchmark

## Dataset Summary

RAT-bench is a curated collection of GitHub repositories across multiple programming languages (Python, Java, Rust, Node.js), 
each containing Dockerfiles and unit tests. This dataset is designed for evaluating code analysis, Docker containerization, 
and automated testing capabilities.

The dataset is organized by programming language and repository size, providing 16 different configurations 
for flexible use in various research and development scenarios.

## Dataset Configurations

The dataset contains 16 configurations organized by language and size:

| Configuration | Language | Size Category | Count | Size Range | Avg Size |
|--------------|----------|---------------|-------|------------|----------|
"""

    # Add configuration details
    for lang in languages:
        lang_stats = stats.get(lang, {})
        categories = lang_stats.get("categories", {})

        for size in ["small", "medium", "large", "all"]:
            config_name = f"{lang}_{size}"

            if size == "all":
                count = lang_stats.get("total_count", 0)
                size_range = "All sizes"
                avg_size = "-"
            else:
                cat_info = categories.get(size, {})
                count = cat_info.get("count", 0)
                size_range = cat_info.get("size_range", "-")
                avg_bytes = cat_info.get("avg_bytes", 0)
                avg_size = format_bytes(avg_bytes) if avg_bytes > 0 else "-"

            card += f"| `{config_name}` | {lang.capitalize()} | {size.capitalize()} | {count} | {size_range} | {avg_size} |\n"

    card += f"""
## Usage

### Load a specific configuration

```python
from datasets import load_dataset

# Load Python small repositories (< 500 KB)
dataset = load_dataset("{dataset_name}", "python_small")

# Load Java medium repositories (500 KB - 5 MB)
dataset = load_dataset("{dataset_name}", "java_medium")

# Load all Rust repositories
dataset = load_dataset("{dataset_name}", "rust_all")
```

### Load multiple configurations

```python
from datasets import load_dataset, concatenate_datasets

# Load all small repositories across all languages
small_repos = concatenate_datasets([
    load_dataset("{dataset_name}", f"{{lang}}_small")["train"]
    for lang in ["python", "java", "rust", "nodejs"]
])

print(f"Total small repositories: {{len(small_repos)}}")
```

### Iterate through repositories

```python
import json
dataset = load_dataset("{dataset_name}", "python_all")

for repo in dataset["train"]:
    print(f"Repository: {{repo['full_name']}}")
    print(f"Stars: {{repo['stargazers_count']}}")
    print(f"Size: {{repo['total_bytes']}} bytes")
    
    # Parse language breakdown from JSON string
    lang_breakdown = json.loads(repo['language_breakdown_json'])
    print(f"Languages: {{lang_breakdown}}")
    print("---")
```

## Data Fields

Each repository entry contains the following fields:

- `full_name` (string): Repository full name in format "owner/repo"
- `clone_url` (string): Git clone URL (HTTPS)
- `default_branch` (string): Default branch name (e.g., "main", "master")
- `stargazers_count` (int): Number of GitHub stars
- `language` (string): Primary programming language
- `description` (string, nullable): Repository description
- `html_url` (string, nullable): GitHub repository URL
- `forks_count` (int): Number of forks
- `topics` (list[string]): Repository topics/tags
- `total_bytes` (int): Total repository size in bytes (all files)
- `code_bytes` (int): Code-only size in bytes (excluding assets)
- `language_breakdown_json` (string): JSON string of language distribution percentages
  - Example: `{{"Python": 95.5, "JavaScript": 3.2}}`
  - Parse with `json.loads(row["language_breakdown_json"])`
- `size` (string): Size category ("small", "medium", or "large")
- `id` (string): Unique identifier (format: language-size-index)

## Dataset Statistics

### Overall Statistics

"""

    # Add overall stats
    total_repos = sum(stats.get(lang, {}).get("total_count", 0) for lang in languages)
    card += f"- **Total repositories**: {total_repos:,}\n"
    card += f"- **Languages**: {len(languages)} (Python, Java, Rust, Node.js)\n"
    card += f"- **Configurations**: 16 (4 languages × 4 size categories)\n\n"

    card += """### Size Categories

- **Small**: < 500 KB code size
- **Medium**: 500 KB - 5 MB code size  
- **Large**: > 5 MB code size
- **All**: All repositories (small + medium + large)

## Data Collection

The repositories were collected using the GitHub Search API with the following criteria:

1. **Language-specific search**: Repositories must be primarily written in Python, Java, Rust, or JavaScript/TypeScript
2. **Dockerfile requirement**: Must contain a Dockerfile for containerization
3. **Test requirement**: Must contain unit tests (for Python: pytest)
4. **Build tool requirement**: 
   - Java: Maven (pom.xml) or Gradle (build.gradle)
   - Node.js: package.json
   - Rust: Cargo.toml
5. **Activity**: Repositories updated within the last year
6. **Popularity**: Varying star counts (10-50+ stars depending on size category)

### Collection Strategy

To ensure balanced size distribution, we used a stratified sampling approach:
- 40% small repositories (< 1MB on GitHub)
- 35% medium repositories (1-5MB on GitHub)
- 25% large repositories (> 5MB on GitHub)

Repositories are sorted by code size (ascending) to prioritize smaller, more manageable projects.

## Use Cases

This dataset is suitable for:

1. **Docker Build Automation**: Training/evaluating models to generate or fix Dockerfiles
2. **Test Execution**: Analyzing test suites and test execution patterns
3. **Code Analysis**: Multi-language code understanding and analysis
4. **Repository Characterization**: Understanding repository structure and composition
5. **CI/CD Research**: Studying containerization and testing practices
6. **Benchmark Evaluation**: Evaluating code agents, LLMs, and automated tools

## Limitations

- Only includes repositories with Dockerfiles and tests (selection bias)
- Repository metadata is a snapshot from the collection date
- Some repositories may have been updated or deleted since collection
- Size measurements are approximate (based on GitHub's language statistics API)

## Citation

If you use this dataset in your research, please cite:

```bibtex
@dataset{

}
```

## License

This dataset is released under the Apache 2.0 License. Note that individual repositories 
may have their own licenses - please check each repository's license before use.

## Acknowledgments

Data collected from GitHub using the GitHub API. Repository metadata is provided by GitHub, Inc.
"""

    return card


def format_bytes(num_bytes: int) -> str:
    """Format bytes as human-readable string."""
    for unit in ["bytes", "KB", "MB", "GB"]:
        if num_bytes < 1024.0:
            return f"{num_bytes:,.0f} {unit}"
        num_bytes /= 1024.0
    return f"{num_bytes:,.0f} TB"


def create_eval_subsets(
    dataset_name: str,
    languages: List[str],
    sample_size: int,
    seed: int,
    token: str,
) -> Dict[str, Dataset]:
    """
    Create evaluation subsets by randomly sampling from full datasets on HuggingFace.

    Args:
        dataset_name: HuggingFace dataset name (e.g., "<omitted>")
        languages: List of languages to process
        sample_size: Number of examples to sample per language
        seed: Random seed for reproducibility
        token: HuggingFace API token

    Returns:
        Dict mapping "{language}_eval" to sampled Dataset
    """
    print("\n" + "=" * 60)
    print("Creating evaluation subsets from HuggingFace datasets")
    print("=" * 60)
    print(f"Source dataset: {dataset_name}")
    print(f"Sample size: {sample_size} per language")
    print(f"Random seed: {seed}")
    print("=" * 60 + "\n")

    eval_datasets = {}
    random.seed(seed)

    for lang in languages:
        config_name = f"{lang}_all"
        eval_config_name = f"{lang}_eval"

        print(f"Processing {lang}...")

        try:
            # Load full dataset from HuggingFace using split name (not config)
            print(f"  Loading split '{config_name}' from HuggingFace...")
            full_dataset = load_dataset(
                dataset_name,
                split=config_name,
                token=token,
            )
            total = len(full_dataset)
            print(f"  ✓ Loaded {total:,} examples")

            # Random sampling
            if total < sample_size:
                print(f"  ⚠ Warning: Only {total} examples available, using all")
                sampled = full_dataset
            else:
                print(f"  Sampling {sample_size} random examples...")
                indices = random.sample(range(total), sample_size)
                indices.sort()  # Sort for better cache locality
                sampled = full_dataset.select(indices)
                print(f"  ✓ Sampled {len(sampled)} examples")

            # Update IDs to reflect eval subset
            def update_id(example, idx):
                example["id"] = f"{lang}_eval_{idx}"
                return example

            sampled = sampled.map(update_id, with_indices=True)

            eval_datasets[eval_config_name] = sampled
            print(f"  ✓ {eval_config_name} created successfully\n")

        except Exception as e:
            print(f"  ✗ Failed to create {eval_config_name}: {e}\n", file=sys.stderr)
            continue

    return eval_datasets


def main():
    parser = argparse.ArgumentParser(
        description="Upload repository datasets to Hugging Face Hub",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--dataset-name",
        type=str,
        default="<omitted>",
        help="Dataset name on Hugging Face Hub (default: <omitted>)",
    )

    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path("datasets"),
        help="Directory containing language subdirectories (default: datasets)",
    )

    parser.add_argument(
        "--private",
        action="store_true",
        help="Make the dataset private",
    )

    parser.add_argument(
        "--languages",
        type=str,
        nargs="+",
        default=["python", "java", "rust", "nodejs"],
        help="Languages to include (default: python java rust nodejs)",
    )

    parser.add_argument(
        "--create-eval",
        action="store_true",
        help="Create evaluation subsets by randomly sampling from full datasets",
    )

    parser.add_argument(
        "--eval-sample-size",
        type=int,
        default=150,
        help="Number of examples to sample for eval subsets (default: 150)",
    )

    parser.add_argument(
        "--eval-seed",
        type=int,
        default=42,
        help="Random seed for eval sampling (default: 42)",
    )

    args = parser.parse_args()

    # Load environment variables
    load_dotenv()
    token = os.getenv("HF_TOKEN")

    if not token:
        print("Error: HF_TOKEN not found in environment variables", file=sys.stderr)
        print("Please set HF_TOKEN in .env file or environment", file=sys.stderr)
        sys.exit(1)

    # Validate data directory
    if not args.data_dir.exists():
        print(f"Error: Data directory not found: {args.data_dir}", file=sys.stderr)
        sys.exit(1)

    print("=" * 60)
    print(f"Publishing dataset to Hugging Face: {args.dataset_name}")
    print("=" * 60)

    # Collect statistics
    stats = {}
    for lang in args.languages:
        summary_file = args.data_dir / lang / f"{lang}_repos_summary.json"
        stats[lang] = load_summary(summary_file)

    # Create repository if it doesn't exist
    api = HfApi(token=token)
    print(f"\nVerifying repository: {args.dataset_name}")
    try:
        create_repo(
            repo_id=args.dataset_name,
            token=token,
            repo_type="dataset",
            private=args.private,
            exist_ok=True,
        )
        print(f"✓ Repository verified")
    except Exception as e:
        print(f"Warning: Could not verify repository: {e}", file=sys.stderr)
        print(f"Continuing anyway (repository may already exist)...", file=sys.stderr)

    # Create eval subsets if requested
    if args.create_eval:
        eval_datasets = create_eval_subsets(
            dataset_name=args.dataset_name,
            languages=args.languages,
            sample_size=args.eval_sample_size,
            seed=args.eval_seed,
            token=token,
        )

        if not eval_datasets:
            print("Error: No eval datasets created", file=sys.stderr)
            sys.exit(1)

        # Load existing dataset to preserve all splits
        print(f"\nLoading existing dataset to preserve all splits...")
        try:
            existing_dataset = load_dataset(args.dataset_name, token=token)
            print(f"  ✓ Loaded existing dataset with {len(existing_dataset)} splits")
        except Exception as e:
            print(f"  ⚠ Could not load existing dataset: {e}")
            print(f"  Creating new dataset dict...")
            existing_dataset = DatasetDict()

        # Add eval datasets as new splits
        print(f"\nAdding eval subsets as new splits...")
        for eval_name, eval_ds in eval_datasets.items():
            existing_dataset[eval_name] = eval_ds
            print(f"  ✓ Added {eval_name}: {len(eval_ds):,} examples")

        # Upload complete dataset with all splits
        print(f"\nUploading complete dataset to Hugging Face Hub...")
        try:
            existing_dataset.push_to_hub(
                repo_id=args.dataset_name,
                token=token,
                private=args.private,
            )
            print(
                f"  ✓ Dataset uploaded successfully with {len(existing_dataset)} splits"
            )
        except Exception as e:
            print(f"  ✗ Failed to upload dataset: {e}", file=sys.stderr)
            import traceback

            traceback.print_exc()

        print("\n" + "=" * 60)
        print("✓ Eval subsets created and uploaded successfully!")
        print("=" * 60)
        print(f"\nDataset URL: https://huggingface.co/datasets/{args.dataset_name}")
        print(f"\nEval configurations uploaded: {len(eval_datasets)}")
        for config_name in sorted(eval_datasets.keys()):
            count = len(eval_datasets[config_name])
            print(f"  - {config_name}: {count:,} examples")

        return 0

    # Build dataset dictionary
    print(f"\nLoading datasets...")
    dataset_dict = {}

    for lang in args.languages:
        lang_dir = args.data_dir / lang

        if not lang_dir.exists():
            print(f"Warning: Skipping {lang} (directory not found)", file=sys.stderr)
            continue

        for size in ["small", "medium", "large", "all"]:
            # Use underscore instead of hyphen for split names (HF requirement)
            config_name = f"{lang}_{size}"
            json_file = lang_dir / f"{lang}_repos_{size}.json"

            if not json_file.exists():
                print(
                    f"Warning: Skipping {config_name} (file not found)", file=sys.stderr
                )
                continue

            print(f"  Loading {config_name}...")
            try:
                data = load_json_to_list(json_file)

                # Add unique ID and normalize data to ensure consistent schema
                for idx, row in enumerate(data):
                    row["id"] = f"{lang}_{size}_{idx}"

                    # Convert language_breakdown dict to JSON string to avoid schema conflicts
                    # Different repos have different languages, causing schema mismatches
                    if "language_breakdown" in row and isinstance(
                        row["language_breakdown"], dict
                    ):
                        row["language_breakdown_json"] = json.dumps(
                            row["language_breakdown"]
                        )
                        del row["language_breakdown"]
                    else:
                        row["language_breakdown_json"] = "{}"

                    # Ensure description is string or None (not missing)
                    if "description" not in row:
                        row["description"] = None

                    # Ensure html_url is string or None
                    if "html_url" not in row:
                        row["html_url"] = None

                    # Ensure topics is a list
                    if "topics" not in row:
                        row["topics"] = []
                    elif row["topics"] is None:
                        row["topics"] = []

                # Create dataset
                dataset = Dataset.from_list(data)
                dataset_dict[config_name] = dataset

                print(f"    ✓ Loaded {len(dataset)} rows")
            except Exception as e:
                print(f"    ✗ Error loading {config_name}: {e}", file=sys.stderr)

    if not dataset_dict:
        print("Error: No datasets loaded", file=sys.stderr)
        sys.exit(1)

    # Create DatasetDict
    print(f"\nCreating DatasetDict with {len(dataset_dict)} configurations...")
    ds_dict = DatasetDict(dataset_dict)

    # Generate dataset card
    print(f"\nGenerating dataset card...")
    card_content = create_dataset_card(
        dataset_name=args.dataset_name,
        data_dir=args.data_dir,
        languages=args.languages,
        stats=stats,
    )

    # Upload to Hub
    print(f"\nUploading to Hugging Face Hub...")
    try:
        ds_dict.push_to_hub(
            repo_id=args.dataset_name,
            token=token,
            private=args.private,
        )
        print(f"✓ Datasets uploaded successfully")

        # Upload README
        print(f"\nUploading dataset card (README.md)...")
        api.upload_file(
            path_or_fileobj=card_content.encode("utf-8"),
            path_in_repo="README.md",
            repo_id=args.dataset_name,
            repo_type="dataset",
            token=token,
        )
        print(f"✓ Dataset card uploaded")

    except Exception as e:
        print(f"Error uploading to Hub: {e}", file=sys.stderr)
        sys.exit(1)

    print("\n" + "=" * 60)
    print("✓ Dataset published successfully!")
    print("=" * 60)
    print(f"\nDataset URL: https://huggingface.co/datasets/{args.dataset_name}")
    print(f"\nConfigurations uploaded: {len(dataset_dict)}")
    for config_name in sorted(dataset_dict.keys()):
        count = len(dataset_dict[config_name])
        print(f"  - {config_name}: {count:,} repositories")

    print(f"\nTo use this dataset:")
    print(f"  from datasets import load_dataset")
    print(f'  ds = load_dataset("{args.dataset_name}", "python_small")')

    return 0


if __name__ == "__main__":
    sys.exit(main())
