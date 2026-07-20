import weave
import json
import argparse
import os
from pathlib import Path
from weave import Dataset


def load_json_file(file_path):
    """Load and validate JSON file."""
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"File not found: {file_path}")

    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, list):
        raise ValueError("JSON file must contain an array of objects")

    processed_data = []
    for index, row in enumerate(data):
        if isinstance(row, dict):
            new_row = {"id": index, **row}
            processed_data.append(new_row)
        else:
            continue

    return processed_data


def publish_dataset(file_path, dataset_name, project_name):
    """Load JSON file and publish as Weave dataset."""
    print(f"Loading data from {file_path}...")
    rows = load_json_file(file_path)
    print(f"Loaded {len(rows)} rows")

    # Initialize Weave
    print(f"Initializing Weave project: {project_name}")
    weave.init(project_name)

    # Create dataset
    print(f"Creating dataset: {dataset_name}")
    dataset = Dataset(name=dataset_name, rows=rows)

    # Publish the dataset
    print("Publishing dataset...")
    weave.publish(dataset)
    print(f"✓ Dataset '{dataset_name}' published successfully!")

    # Show how to retrieve the dataset
    print(f"\nTo retrieve this dataset, use:")
    print(f"  dataset_ref = weave.ref('{dataset_name}').get()")
    print(f"  rows = dataset_ref.rows")

    return dataset


def main():
    parser = argparse.ArgumentParser(
        description="Publish local JSON file as Weave dataset",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Use default file (datasets/all_repos.json)
  python datasets/publish_datasets.py
  
  # Specify custom file and dataset name
  python datasets/publish_datasets.py --file data/repos.json --name github-repos
  
  # Specify all parameters
  python datasets/publish_datasets.py -f data.json -n my-dataset -p my-project
        """,
    )

    parser.add_argument(
        "-f",
        "--file",
        type=str,
        default="datasets/all_repos.json",
        help="Path to JSON file (default: datasets/all_repos.json)",
    )

    parser.add_argument(
        "-n",
        "--name",
        type=str,
        default=None,
        help="Dataset name (default: derived from filename)",
    )

    parser.add_argument(
        "-p",
        "--project",
        type=str,
        default="repo-datasets",
        help="Weave project name (default: repo-datasets)",
    )

    args = parser.parse_args()

    # Derive dataset name from filename if not provided
    if args.name is None:
        file_stem = Path(args.file).stem
        args.name = file_stem

    try:
        publish_dataset(args.file, args.name, args.project)
    except FileNotFoundError as e:
        print(f"Error: {e}")
        exit(1)
    except ValueError as e:
        print(f"Error: {e}")
        exit(1)
    except Exception as e:
        print(f"Unexpected error: {e}")
        exit(1)


if __name__ == "__main__":
    main()
