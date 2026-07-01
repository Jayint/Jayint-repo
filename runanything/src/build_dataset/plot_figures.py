#!/usr/bin/env python3
"""
Generate ICML-style vector plots for dataset distribution analysis

Creates publication-quality figures showing:
1. Repository size distribution across languages (bar chart)
2. Average repository sizes with error bars (comparison chart)
3. Distribution heatmap (percentage view)
4. Stars distribution histogram across all languages (log scale)
5. Stars distribution by language (box plot)
6. Stars vs repository size scatter plot (log-log scale)
7. Stars distribution by size category (box plot)

All figures follow ICML formatting guidelines with vector output (PDF + PNG).

Usage:
    python3 plot_figures.py                    # Generate all plots
    python3 plot_figures.py --size-only        # Only size distribution plots (1-3)
    python3 plot_figures.py --stars-only       # Only stars analysis plots (4-7)
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, List, Tuple

import matplotlib.pyplot as plt
import matplotlib as mpl
import numpy as np


# ============ Configuration ============


def configure_icml_style():
    """Configure matplotlib for ICML publication style"""
    mpl.rcParams["font.family"] = "serif"
    mpl.rcParams["font.serif"] = ["Times New Roman", "Times", "DejaVu Serif"]
    mpl.rcParams["font.size"] = 9
    mpl.rcParams["axes.linewidth"] = 0.8
    mpl.rcParams["axes.labelsize"] = 9
    mpl.rcParams["axes.titlesize"] = 10
    mpl.rcParams["xtick.labelsize"] = 8
    mpl.rcParams["ytick.labelsize"] = 8
    mpl.rcParams["legend.fontsize"] = 8
    mpl.rcParams["xtick.major.width"] = 0.8
    mpl.rcParams["ytick.major.width"] = 0.8
    mpl.rcParams["xtick.major.size"] = 3
    mpl.rcParams["ytick.major.size"] = 3
    mpl.rcParams["lines.linewidth"] = 1.0
    mpl.rcParams["patch.linewidth"] = 0.5
    mpl.rcParams["grid.linewidth"] = 0.5
    mpl.rcParams["grid.alpha"] = 0.3
    # Ensure TrueType fonts for vector output
    mpl.rcParams["pdf.fonttype"] = 42
    mpl.rcParams["ps.fonttype"] = 42
    mpl.rcParams["savefig.dpi"] = 300
    mpl.rcParams["savefig.bbox"] = "tight"
    mpl.rcParams["savefig.pad_inches"] = 0.05


# Single column width for ICML (in inches)
SINGLE_COLUMN_WIDTH = 3.25

# Colorblind-friendly palette (from ColorBrewer)
SIZE_COLORS = {
    "small": "#8dd3c7",  # light teal
    "medium": "#bebada",  # light purple
    "large": "#fb8072",  # light red
}

LANG_COLORS = {
    "Python": "#1f77b4",  # blue
    "Java": "#ff7f0e",  # orange
    "JS/TS": "#2ca02c",  # green
    "Rust": "#d62728",  # red
}

SIZE_LABELS = {
    "small": "Small (< 500 KB)",
    "medium": "Medium (500 KB - 5 MB)",
    "large": "Large (> 5 MB)",
}


# ============ Data Loading ============


def load_dataset_summaries(base_dir: Path) -> Dict[str, Dict]:
    """Load summary JSON files for all languages

    Args:
        base_dir: Base datasets directory

    Returns:
        Dict mapping language name to summary data
    """
    summaries = {}

    lang_files = {
        "Python": base_dir / "python" / "python_repos_summary.json",
        "Java": base_dir / "java" / "java_repos_summary.json",
        "JS/TS": base_dir / "nodejs" / "nodejs_repos_summary.json",
        "Rust": base_dir / "rust" / "rust_repos_summary.json",
    }

    for lang, file_path in lang_files.items():
        if file_path.exists():
            with open(file_path, "r", encoding="utf-8") as f:
                summaries[lang] = json.load(f)

    return summaries


def load_all_repos(base_dir: Path) -> Dict[str, List[Dict]]:
    """Load all repository data for each language

    Args:
        base_dir: Base datasets directory

    Returns:
        Dict mapping language name to list of repository data
    """
    repos = {}

    lang_files = {
        "Python": base_dir / "python" / "python_repos_all.json",
        "Java": base_dir / "java" / "java_repos_all.json",
        "JS/TS": base_dir / "nodejs" / "nodejs_repos_all.json",
        "Rust": base_dir / "rust" / "rust_repos_all.json",
    }

    for lang, file_path in lang_files.items():
        if file_path.exists():
            with open(file_path, "r", encoding="utf-8") as f:
                repos[lang] = json.load(f)

    return repos


# ============ Size Distribution Plots ============


def plot_size_distribution(summaries: Dict[str, Dict], output_dir: Path):
    """Create grouped bar chart showing repository size distribution

    Args:
        summaries: Dictionary of language summaries
        output_dir: Output directory for figures
    """
    languages = list(summaries.keys())
    size_categories = ["small", "medium", "large"]

    # Extract counts
    data = {cat: [] for cat in size_categories}
    percentages = {cat: [] for cat in size_categories}

    for lang in languages:
        total = summaries[lang]["total_count"]
        for cat in size_categories:
            count = summaries[lang]["categories"][cat]["count"]
            data[cat].append(count)
            percentages[cat].append((count / total) * 100)

    # Create figure
    fig, ax = plt.subplots(figsize=(SINGLE_COLUMN_WIDTH, 2.5))

    # Bar positions
    x = np.arange(len(languages))
    width = 0.25

    # Plot bars
    for i, cat in enumerate(size_categories):
        offset = width * (i - 1)
        bars = ax.bar(
            x + offset,
            data[cat],
            width,
            label=SIZE_LABELS[cat],
            color=SIZE_COLORS[cat],
            edgecolor="black",
            linewidth=0.5,
        )

        # Add count and percentage labels
        # for j, (bar, count, pct) in enumerate(zip(bars, data[cat], percentages[cat])):
        #     height = bar.get_height()
        #     ax.text(
        #         bar.get_x() + bar.get_width() / 2,
        #         height + 5,
        #         f"{count}\n({pct:.1f}%)",
        #         ha="center",
        #         va="bottom",
        #         fontsize=6.5,
        #         fontweight="normal",
        #     )

    # Customize plot
    ax.set_xlabel("Programming Language", fontweight="bold")
    ax.set_ylabel("Number of Repositories", fontweight="bold")
    # ax.set_title(
    #     "Repository Size Distribution Across Languages", fontweight="bold", pad=10
    # )
    ax.set_xticks(x)
    ax.set_xticklabels(languages)
    ax.legend(loc="upper right", frameon=True, fancybox=False, edgecolor="black")
    ax.set_ylim(0, max(max(data[cat]) for cat in size_categories) * 1.25)
    ax.grid(axis="y", alpha=0.3, linestyle="--", linewidth=0.5)
    ax.set_axisbelow(True)

    # Save figure
    plt.tight_layout()
    fig.savefig(
        output_dir / "size_distribution_bar.pdf", format="pdf", bbox_inches="tight"
    )
    fig.savefig(
        output_dir / "size_distribution_bar.png",
        format="png",
        dpi=300,
        bbox_inches="tight",
    )
    plt.close(fig)

    print(f"✓ Created: size_distribution_bar.pdf/.png")


def plot_average_size_comparison(summaries: Dict[str, Dict], output_dir: Path):
    """Create bar chart comparing average repository sizes with error bars

    Args:
        summaries: Dictionary of language summaries
        output_dir: Output directory for figures
    """
    languages = list(summaries.keys())
    size_categories = ["small", "medium", "large"]

    # Extract average sizes and ranges (in MB)
    data = {cat: [] for cat in size_categories}
    errors_low = {cat: [] for cat in size_categories}
    errors_high = {cat: [] for cat in size_categories}

    for lang in languages:
        for cat in size_categories:
            cat_data = summaries[lang]["categories"][cat]
            avg = cat_data["avg_bytes"] / (1024 * 1024)  # Convert to MB
            min_val = cat_data["min_bytes"] / (1024 * 1024)
            max_val = cat_data["max_bytes"] / (1024 * 1024)

            data[cat].append(avg)
            errors_low[cat].append(avg - min_val)
            errors_high[cat].append(max_val - avg)

    # Create figure
    fig, ax = plt.subplots(figsize=(SINGLE_COLUMN_WIDTH, 2.5))

    # Bar positions
    x = np.arange(len(languages))
    width = 0.25

    # Plot bars with error bars
    for i, cat in enumerate(size_categories):
        offset = width * (i - 1)
        errors = [errors_low[cat], errors_high[cat]]

        ax.bar(
            x + offset,
            data[cat],
            width,
            label=SIZE_LABELS[cat],
            color=SIZE_COLORS[cat],
            edgecolor="black",
            linewidth=0.5,
            yerr=errors,
            error_kw={
                "linewidth": 0.8,
                "capsize": 2,
                "capthick": 0.8,
                "ecolor": "black",
            },
        )

    # Customize plot
    ax.set_xlabel("Programming Language", fontweight="bold")
    ax.set_ylabel("Average Repository Size (MB)", fontweight="bold")
    # ax.set_title(
    #     "Average Repository Sizes with Min/Max Range", fontweight="bold", pad=10
    # )
    ax.set_xticks(x)
    ax.set_xticklabels(languages)
    ax.legend(loc="upper left", frameon=True, fancybox=False, edgecolor="black")
    ax.set_yscale("log")  # Log scale due to large variation
    ax.grid(axis="y", alpha=0.3, linestyle="--", linewidth=0.5, which="both")
    ax.set_axisbelow(True)

    # Save figure
    plt.tight_layout()
    fig.savefig(
        output_dir / "average_size_comparison.pdf", format="pdf", bbox_inches="tight"
    )
    fig.savefig(
        output_dir / "average_size_comparison.png",
        format="png",
        dpi=300,
        bbox_inches="tight",
    )
    plt.close(fig)

    print(f"✓ Created: average_size_comparison.pdf/.png")


def plot_distribution_heatmap(summaries: Dict[str, Dict], output_dir: Path):
    """Create heatmap showing percentage distribution across languages and sizes

    Args:
        summaries: Dictionary of language summaries
        output_dir: Output directory for figures
    """
    languages = list(summaries.keys())
    size_categories = ["small", "medium", "large"]

    # Build percentage matrix (languages × size categories)
    matrix = []
    for lang in languages:
        row = []
        total = summaries[lang]["total_count"]
        for cat in size_categories:
            count = summaries[lang]["categories"][cat]["count"]
            pct = (count / total) * 100
            row.append(pct)
        matrix.append(row)

    matrix = np.array(matrix)

    # Create figure
    fig, ax = plt.subplots(figsize=(SINGLE_COLUMN_WIDTH, 2.8))

    # Create heatmap
    im = ax.imshow(matrix, cmap="YlOrRd", aspect="auto", vmin=0, vmax=100)

    # Set ticks and labels
    ax.set_xticks(np.arange(len(size_categories)))
    ax.set_yticks(np.arange(len(languages)))
    ax.set_xticklabels(["Small", "Medium", "Large"])
    ax.set_yticklabels(languages)

    # Add text annotations
    for i in range(len(languages)):
        for j in range(len(size_categories)):
            count = summaries[languages[i]]["categories"][size_categories[j]]["count"]
            pct = matrix[i, j]
            text = ax.text(
                j,
                i,
                f"{count}\n({pct:.1f}%)",
                ha="center",
                va="center",
                color="black" if pct < 50 else "white",
                fontsize=7.5,
                fontweight="bold",
            )

    # Add colorbar
    cbar = plt.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    cbar.set_label("Percentage (%)", rotation=270, labelpad=15, fontweight="bold")

    # Customize plot
    ax.set_xlabel("Repository Size Category", fontweight="bold")
    ax.set_ylabel("Programming Language", fontweight="bold")
    # ax.set_title("Repository Size Distribution Heatmap", fontweight="bold", pad=10)

    # Add grid
    ax.set_xticks(np.arange(len(size_categories) + 1) - 0.5, minor=True)
    ax.set_yticks(np.arange(len(languages) + 1) - 0.5, minor=True)
    ax.grid(which="minor", color="white", linestyle="-", linewidth=1.5)
    ax.tick_params(which="minor", size=0)

    # Save figure
    plt.tight_layout()
    fig.savefig(
        output_dir / "distribution_heatmap.pdf", format="pdf", bbox_inches="tight"
    )
    fig.savefig(
        output_dir / "distribution_heatmap.png",
        format="png",
        dpi=300,
        bbox_inches="tight",
    )
    plt.close(fig)

    print(f"✓ Created: distribution_heatmap.pdf/.png")


# ============ Stars Distribution Plots ============


def plot_stars_histogram(repos_dict: Dict[str, List[Dict]], output_dir: Path):
    """Create histogram showing overall stars distribution across all languages

    Args:
        repos_dict: Dictionary of language -> repos list
        output_dir: Output directory for figures
    """
    # Collect all stars
    all_stars = []
    for repos in repos_dict.values():
        all_stars.extend([repo["stargazers_count"] for repo in repos])

    all_stars = np.array(all_stars)

    # Create figure
    fig, ax = plt.subplots(figsize=(SINGLE_COLUMN_WIDTH, 2.5))

    # Create logarithmic bins
    bins = np.logspace(np.log10(min(all_stars)), np.log10(max(all_stars)), 30)

    # Plot histogram
    ax.hist(
        all_stars,
        bins=bins,
        color="#3498db",
        edgecolor="black",
        linewidth=0.5,
        alpha=0.7,
    )

    # Customize plot
    ax.set_xscale("log")
    ax.set_xlabel("Number of Stars (log scale)", fontweight="bold")
    ax.set_ylabel("Number of Repositories", fontweight="bold")
    # ax.set_title(
    #     "Distribution of Repository Popularity (Stars)", fontweight="bold", pad=10
    # )
    ax.grid(axis="y", alpha=0.3, linestyle="--", linewidth=0.5)
    ax.set_axisbelow(True)

    # Add statistics text
    median_stars = np.median(all_stars)
    mean_stars = np.mean(all_stars)
    stats_text = f"Median: {int(median_stars)}\nMean: {int(mean_stars)}"
    ax.text(
        0.97,
        0.97,
        stats_text,
        transform=ax.transAxes,
        verticalalignment="top",
        horizontalalignment="right",
        bbox=dict(
            boxstyle="round",
            facecolor="white",
            alpha=0.8,
            edgecolor="black",
            linewidth=0.5,
        ),
        fontsize=7,
    )

    # Save figure
    plt.tight_layout()
    fig.savefig(output_dir / "stars_histogram.pdf", format="pdf", bbox_inches="tight")
    fig.savefig(
        output_dir / "stars_histogram.png", format="png", dpi=300, bbox_inches="tight"
    )
    plt.close(fig)

    print(f"✓ Created: stars_histogram.pdf/.png")


def plot_stars_by_language(repos_dict: Dict[str, List[Dict]], output_dir: Path):
    """Create box plot showing stars distribution by language

    Args:
        repos_dict: Dictionary of language -> repos list
        output_dir: Output directory for figures
    """
    languages = list(repos_dict.keys())
    stars_data = {
        lang: [repo["stargazers_count"] for repo in repos]
        for lang, repos in repos_dict.items()
    }

    # Prepare data for box plot
    data_to_plot = [stars_data[lang] for lang in languages]

    # Create figure
    fig, ax = plt.subplots(figsize=(SINGLE_COLUMN_WIDTH, 2.8))

    # Create box plot
    bp = ax.boxplot(
        data_to_plot,
        tick_labels=languages,
        patch_artist=True,
        medianprops=dict(color="black", linewidth=1.5),
        flierprops=dict(marker="o", markerfacecolor="gray", markersize=2, alpha=0.3),
        boxprops=dict(linewidth=0.8),
        whiskerprops=dict(linewidth=0.8),
        capprops=dict(linewidth=0.8),
    )

    # Color boxes
    for patch, lang in zip(bp["boxes"], languages):
        patch.set_facecolor(LANG_COLORS[lang])
        patch.set_alpha(0.7)

    # Customize plot
    ax.set_yscale("log")
    ax.set_xlabel("Programming Language", fontweight="bold")
    ax.set_ylabel("Number of Stars (log scale)", fontweight="bold")
    # ax.set_title(
    #     "Repository Popularity Distribution by Language", fontweight="bold", pad=10
    # )
    ax.grid(axis="y", alpha=0.3, linestyle="--", linewidth=0.5, which="both")
    ax.set_axisbelow(True)

    # Add statistics for each language
    # stats_y_pos = ax.get_ylim()[0] * 0.7
    # for i, lang in enumerate(languages):
    #     median_val = int(np.median(stars_data[lang]))
    #     ax.text(
    #         i + 1,
    #         stats_y_pos,
    #         f"Med: {median_val}",
    #         ha="center",
    #         va="top",
    #         fontsize=6.5,
    #         fontweight="bold",
    #         bbox=dict(
    #             boxstyle="round,pad=0.3", facecolor="white", alpha=0.8, linewidth=0.5
    #         ),
    #     )

    # Save figure
    plt.tight_layout()
    fig.savefig(output_dir / "stars_by_language.pdf", format="pdf", bbox_inches="tight")
    fig.savefig(
        output_dir / "stars_by_language.png", format="png", dpi=300, bbox_inches="tight"
    )
    plt.close(fig)

    print(f"✓ Created: stars_by_language.pdf/.png")


def plot_stars_vs_size(repos_dict: Dict[str, List[Dict]], output_dir: Path):
    """Create scatter plot showing relationship between stars and repository size

    Args:
        repos_dict: Dictionary of language -> repos list
        output_dir: Output directory for figures
    """
    fig, ax = plt.subplots(figsize=(SINGLE_COLUMN_WIDTH, 2.8))

    # Plot data for each language
    for lang, repos in repos_dict.items():
        stars = []
        sizes = []
        for repo in repos:
            star_count = repo["stargazers_count"]
            size_mb = repo["code_bytes"] / (1024 * 1024)  # Convert to MB
            if star_count > 0 and size_mb > 0:  # Avoid log(0)
                stars.append(star_count)
                sizes.append(size_mb)

        ax.scatter(
            sizes,
            stars,
            c=LANG_COLORS[lang],
            label=lang,
            alpha=0.5,
            s=15,
            edgecolors="none",
        )

    # Customize plot
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlabel("Repository Code Size (MB, log scale)", fontweight="bold")
    ax.set_ylabel("Number of Stars (log scale)", fontweight="bold")
    # ax.set_title("Repository Popularity vs Size", fontweight="bold", pad=10)
    ax.legend(
        loc="lower right",
        frameon=True,
        fancybox=False,
        edgecolor="black",
        markerscale=1.5,
    )
    ax.grid(True, alpha=0.3, linestyle="--", linewidth=0.5, which="both")
    ax.set_axisbelow(True)

    # Save figure
    plt.tight_layout()
    fig.savefig(output_dir / "stars_vs_size.pdf", format="pdf", bbox_inches="tight")
    fig.savefig(
        output_dir / "stars_vs_size.png", format="png", dpi=300, bbox_inches="tight"
    )
    plt.close(fig)

    print(f"✓ Created: stars_vs_size.pdf/.png")


def plot_stars_by_size_category(repos_dict: Dict[str, List[Dict]], output_dir: Path):
    """Create box plot showing stars distribution by repository size category

    Args:
        repos_dict: Dictionary of language -> repos list
        output_dir: Output directory for figures
    """
    # Collect data by size category
    size_categories = ["small", "medium", "large"]
    stars_by_size = {cat: [] for cat in size_categories}

    for repos in repos_dict.values():
        for repo in repos:
            size_cat = repo.get("size", "unknown")
            if size_cat in size_categories:
                stars_by_size[size_cat].append(repo["stargazers_count"])

    # Prepare data for box plot
    data_to_plot = [stars_by_size[cat] for cat in size_categories]
    labels = ["Small\n(< 500 KB)", "Medium\n(500 KB - 5 MB)", "Large\n(> 5 MB)"]

    # Create figure
    fig, ax = plt.subplots(figsize=(SINGLE_COLUMN_WIDTH, 2.8))

    # Create box plot
    bp = ax.boxplot(
        data_to_plot,
        tick_labels=labels,
        patch_artist=True,
        medianprops=dict(color="black", linewidth=1.5),
        flierprops=dict(marker="o", markerfacecolor="gray", markersize=2, alpha=0.3),
        boxprops=dict(linewidth=0.8),
        whiskerprops=dict(linewidth=0.8),
        capprops=dict(linewidth=0.8),
    )

    # Color boxes
    colors = ["#8dd3c7", "#bebada", "#fb8072"]  # Same as size distribution plot
    for patch, color in zip(bp["boxes"], colors):
        patch.set_facecolor(color)
        patch.set_alpha(0.7)

    # Customize plot
    ax.set_yscale("log")
    ax.set_xlabel("Repository Size Category", fontweight="bold")
    ax.set_ylabel("Number of Stars (log scale)", fontweight="bold")
    # ax.set_title("Repository Popularity by Size Category", fontweight="bold", pad=10)
    ax.grid(axis="y", alpha=0.3, linestyle="--", linewidth=0.5, which="both")
    ax.set_axisbelow(True)

    # Add statistics
    # stats_y_pos = ax.get_ylim()[0] * 0.7
    # for i, cat in enumerate(size_categories):
    #     median_val = int(np.median(stars_by_size[cat]))
    #     count = len(stars_by_size[cat])
    #     ax.text(
    #         i + 1,
    #         stats_y_pos,
    #         f"Med: {median_val}\nn={count}",
    #         ha="center",
    #         va="top",
    #         fontsize=6.5,
    #         fontweight="bold",
    #         bbox=dict(
    #             boxstyle="round,pad=0.3", facecolor="white", alpha=0.8, linewidth=0.5
    #         ),
    #     )

    # Save figure
    plt.tight_layout()
    fig.savefig(
        output_dir / "stars_by_size_category.pdf", format="pdf", bbox_inches="tight"
    )
    fig.savefig(
        output_dir / "stars_by_size_category.png",
        format="png",
        dpi=300,
        bbox_inches="tight",
    )
    plt.close(fig)

    print(f"✓ Created: stars_by_size_category.pdf/.png")


# ============ Main Function ============


def parse_args():
    """Parse command line arguments"""
    parser = argparse.ArgumentParser(
        description="Generate ICML-style plots for dataset distribution analysis",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--size-only",
        action="store_true",
        help="Generate only size distribution plots (1-3)",
    )
    parser.add_argument(
        "--stars-only",
        action="store_true",
        help="Generate only stars analysis plots (4-7)",
    )
    return parser.parse_args()


def main():
    """Main function to generate all plots"""
    args = parse_args()

    # Get base directory
    script_dir = Path(__file__).parent
    datasets_dir = script_dir.parent / "datasets"
    output_dir = script_dir / "figures"

    # Create output directory
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"{'=' * 60}")
    print(f"Generating ICML-Style Dataset Distribution Plots")
    print(f"{'=' * 60}")
    print(f"Datasets directory: {datasets_dir}")
    print(f"Output directory: {output_dir}")
    print(f"{'=' * 60}\n")

    # Configure matplotlib style
    configure_icml_style()

    # Determine which plots to generate
    generate_size = not args.stars_only
    generate_stars = not args.size_only

    # Generate size distribution plots (1-3)
    if generate_size:
        print("Loading dataset summaries...")
        summaries = load_dataset_summaries(datasets_dir)

        if not summaries:
            print("Warning: No dataset summary files found! Skipping size plots.")
        else:
            print(
                f"Loaded {len(summaries)} language datasets: {', '.join(summaries.keys())}\n"
            )
            print("Generating size distribution plots...\n")

            plot_size_distribution(summaries, output_dir)
            plot_average_size_comparison(summaries, output_dir)
            plot_distribution_heatmap(summaries, output_dir)

    # Generate stars analysis plots (4-7)
    if generate_stars:
        print("\nLoading repository data...")
        repos_dict = load_all_repos(datasets_dir)

        if not repos_dict:
            print("Warning: No repository data files found! Skipping stars plots.")
        else:
            total_repos = sum(len(repos) for repos in repos_dict.values())
            print(
                f"Loaded {len(repos_dict)} languages with {total_repos} total repositories\n"
            )

            # Print summary statistics
            print("Stars Statistics by Language:")
            print(f"{'=' * 60}")
            for lang, repos in repos_dict.items():
                stars = [r["stargazers_count"] for r in repos]
                print(
                    f"{lang:10s}: min={min(stars):6d}, max={max(stars):8d}, "
                    f"median={int(np.median(stars)):5d}, mean={int(np.mean(stars)):6d}"
                )
            print(f"{'=' * 60}\n")

            print("Generating stars analysis plots...\n")

            plot_stars_histogram(repos_dict, output_dir)
            plot_stars_by_language(repos_dict, output_dir)
            plot_stars_vs_size(repos_dict, output_dir)
            plot_stars_by_size_category(repos_dict, output_dir)

    # Print summary
    print(f"\n{'=' * 60}")
    print(f"All plots generated successfully!")
    print(f"{'=' * 60}")
    print(f"Output location: {output_dir}")
    print(f"\nGenerated files:")
    if generate_size:
        print(f"  Size Distribution (1-3):")
        print(f"    - size_distribution_bar.pdf/.png")
        print(f"    - average_size_comparison.pdf/.png")
        print(f"    - distribution_heatmap.pdf/.png")
    if generate_stars:
        print(f"  Stars Analysis (4-7):")
        print(f"    - stars_histogram.pdf/.png")
        print(f"    - stars_by_language.pdf/.png")
        print(f"    - stars_vs_size.pdf/.png")
        print(f"    - stars_by_size_category.pdf/.png")
    print(f"{'=' * 60}")

    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
