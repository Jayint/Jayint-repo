import os
import queue
import subprocess
import sys
import threading
from pathlib import Path

import streamlit as st

from libkit.tools.detect_environment import (
    detect_apt_mirrors,
    detect_gpu,
    detect_installed_tools,
    detect_network,
    detect_python_mirrors,
    detect_system_info,
    format_output,
)


def run_environment_detection():
    """Runs environment detection and returns formatted results."""
    try:
        # Collect environment information
        env_info = {
            "gpu": detect_gpu(),
            "python_mirrors": detect_python_mirrors(),
            "apt_mirrors": detect_apt_mirrors(),
            "system": detect_system_info(),
            "network": detect_network(),
            "tools": detect_installed_tools(),
        }

        # Format output
        return format_output(env_info, output_format="text")
    except Exception as e:
        return f"❌ Environment detection failed: {str(e)}"


# Page configuration
st.set_page_config(
    page_title="RunAnyThing",
    page_icon="🚀",
    layout="wide",
    initial_sidebar_state="expanded",
)


def find_and_read_readme(full_name, root_path="."):
    """Finds and reads the README file of the repository.

    Args:
        full_name: Full repository name, e.g., "owner/repo"
        root_path: Root directory path

    Returns:
        str: README content, or None if not found
    """
    # Build local repository path
    # env_main.py downloads repo to input/repo/{owner}/{repo}/
    owner, repo = full_name.split("/") if "/" in full_name else (full_name, full_name)
    repo_dir = Path(root_path) / "input" / "repo" / owner / repo

    if not repo_dir.exists():
        return None

    # Common README filenames
    readme_patterns = [
        "README.md",
        "README.rst",
        "README.txt",
        "README",
        "readme.md",
        "readme.rst",
        "readme.txt",
        "readme",
    ]

    # Search for README file
    for pattern in readme_patterns:
        readme_path = repo_dir / pattern
        if readme_path.exists() and readme_path.is_file():
            try:
                with open(readme_path, "r", encoding="utf-8") as f:
                    return f.read()
            except UnicodeDecodeError:
                # Try fallback encoding
                try:
                    with open(readme_path, "r", encoding="latin-1") as f:
                        return f.read()
                except:
                    continue
            except Exception:
                continue

    # Use glob to find possible case variations
    readme_files = list(repo_dir.glob("[Rr][Ee][Aa][Dd][Mm][Ee]*"))
    for readme_file in readme_files:
        if readme_file.is_file():
            try:
                with open(readme_file, "r", encoding="utf-8") as f:
                    return f.read()
            except:
                continue

    return None


# Application Title
st.title("🚀 RunAnyThing")
st.markdown("""
### Instructions
1. Fill in all required parameters for `env_main.py`
2. Click the "Run" button to start execution
3. View real-time output below
""")

# Create sidebar for parameter input
with st.sidebar:
    st.header("🔧 Configuration")

    # Required parameters
    full_name = st.text_input(
        "Repository Full Name (full_name)",
        value="streamlit/streamlit-example",
        help="Example: user/repo or streamlit/streamlit-example",
    )

    root_path = st.text_input(
        "Root Path (root_path)",
        value=".",
        help="Project root directory path, defaults to current directory '.'",
    )

    # Optional parameters
    num_turn = st.number_input(
        "Turns (num_turn)",
        min_value=1,
        max_value=100,
        value=15,
        help="Number of turns to execute, default 15",
    )

    llm = st.text_input(
        "LLM Name (llm)",
        value="deepseek-chat",
        help="Name of the LLM model to use, default deepseek-chat",
    )

    # Boolean parameters
    col1, col2 = st.columns(2)
    with col1:
        hitl = st.checkbox(
            "Enable HITL", value=False, help="Enable Human-in-the-Loop mode"
        )

    with col2:
        use_dockerfile = st.checkbox(
            "Use Repo Dockerfile",
            value=False,
            help="Use the Dockerfile included in the repository (if present)",
        )

    # Save mode selection
    save_mode = st.radio(
        "Save Mode (save_mode)",
        options=["none", "dockerfile", "image"],
        index=0,
        help="Save mode: none-do not save, dockerfile-save Dockerfile and files, image-save as local image",
    )

    # Run button
    run_button = st.button("▶️ Run", type="primary", use_container_width=True)

    # Clear button
    if st.button("🧹 Clear Output", use_container_width=True):
        st.session_state.output_text = ""

# Initialize output text state
if "output_text" not in st.session_state:
    st.session_state.output_text = ""

# Create two columns: README on left, Output on right
col_readme, col_output = st.columns([1, 1])

with col_readme:
    st.subheader("📄 Repository README")

    # Attempt to read README
    readme_content = find_and_read_readme(full_name, root_path)

    if readme_content:
        # Display README in a scrollable text area
        st.text_area(
            "README Content",
            value=readme_content,
            height=500,
            disabled=True,
            key="readme_display",
        )
        st.caption(f"📁 Repository: {full_name}")
    else:
        st.info(f"README file for {full_name} not found")
        st.caption(
            "💡 Hint: The README will be downloaded locally after running env_main.py"
        )

with col_output:
    st.subheader("📋 Execution Output")
    output_container = st.container()


# Function to capture output
def stream_output(process, output_queue):
    """Captures output from sub-process and puts it into a queue."""
    while True:
        output = process.stdout.readline()
        if output == "" and process.poll() is not None:
            break
        if output:
            output_queue.put(output)
    process.wait()


# Execute env_main.py
if run_button:
    # Build command
    cmd = [
        sys.executable,
        "env_main.py",
        f"--full_name={full_name}",
        f"--root_path={root_path}",
        f"--num_turn={num_turn}",
        f"--llm={llm}",
    ]

    if hitl:
        cmd.append("--hitl")

    cmd.append(f"--save_mode={save_mode}")

    if use_dockerfile:
        cmd.append("--use-dockerfile")

    # Show the command being executed
    st.info(f"Executing command: `{' '.join(cmd)}`")

    # Clear previous output
    st.session_state.output_text = ""

    # Create output queue
    output_queue = queue.Queue()

    # Setup output area
    output_container.empty()  # Clear container
    output_container.markdown("**Real-time Output**")
    output_placeholder = output_container.empty()

    # Run subprocess
    try:
        # Set environment variables to ensure output is real-time
        env = os.environ.copy()

        # Start subprocess
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            universal_newlines=True,
            env=env,
        )

        # Create thread to capture output
        output_thread = threading.Thread(
            target=stream_output, args=(process, output_queue)
        )
        output_thread.daemon = True
        output_thread.start()

        # Display real-time output
        while output_thread.is_alive() or not output_queue.empty():
            try:
                # Get output from queue
                output = output_queue.get(timeout=0.1)
                if output:
                    st.session_state.output_text += output

                    # Update display - use code block with fixed height
                    output_placeholder.code(
                        st.session_state.output_text,
                        language="bash",
                        line_numbers=False,
                        height=500,
                    )
            except queue.Empty:
                continue
            except Exception as e:
                st.error(f"Error reading output: {e}")
                break

        # Check process return code
        return_code = process.wait()
        if return_code == 0:
            st.success("✅ Execution Complete!")
        else:
            st.error(f"❌ Execution failed with return code: {return_code}")

    except FileNotFoundError:
        st.error(
            "❌ env_main.py file not found. Please ensure it is in the current directory."
        )
    except Exception as e:
        st.error(f"❌ Error occurred during execution: {e}")
else:
    # Display current output content if available
    if st.session_state.output_text:
        output_container.markdown("**Output Content**")
        # Show output in a scrollable text area
        output_container.text_area(
            "Execution Output",
            value=st.session_state.output_text,
            height=500,
            disabled=True,
            key="output_display_static",
        )
    else:
        output_container.info(
            "👆 Click the 'Run' button in the sidebar to start execution"
        )

# Footer
st.markdown("---")
st.caption("RunAnyThing v1.0 | Built with Streamlit")
