# dataabc/weibo-crawler

- **DA pass-rate:** 0/0 (0%) | **RAT pass-rate:** 2/2 (100%) | **bucket:** DA_LOSS
- **DA build_success/test_success:** False / False | **error_breakdown:** docker build failed at Dockerfile line 5 with `/etc/apt/apt.conf.d` directory not found

## Failure stage & category

**Stage:** docker_build  
**Category:** native_system_deps_missing (apt-conf directory missing on Alpine base image)

## Root cause (why DA lost)

The DockerAgent's synthesizer generates a Dockerfile that unconditionally applies apt-based bootstrap configuration (lines 5–8 of generated Dockerfile) without checking the base image type. When the selected base image is Alpine Linux (`python:3.12.0-alpine`), the synthesizer attempts to create `/etc/apt/apt.conf.d/99jayint-retries` using `apt-get` commands. Alpine does not have `apt` or the `/etc/apt/` directory; it uses `apk` instead. The docker build fails immediately with `can't create /etc/apt/apt.conf.d/99jayint-retries: nonexistent directory`. The synthesizer also generates malformed RUN statements (lines 18–20: incomplete `apk add` with trailing backslash but no continuation, followed by separate RUN statements that should be chained with `&&`).

## What RAT did differently

RAT ran in a pre-existing Python environment (not building a Docker image) and directly executed:
- `pip install -q -r /repo/requirements.txt -i https://mirrors.aliyun.com/pypi/simple` (command index 6 in outer_commands.json)
- Then `run-pytest-collect` to discover tests
- Then `run-pytest` to run 2 discovered tests, both passing

RAT did not attempt to generate or build a Dockerfile. It directly executed pip install on the repository's `requirements.txt` and ran pytest in the native environment.

## Evidence

**DA error in Dockerfile build (eval_build/Dockerfile lines 5–8):**
```dockerfile
RUN printf '%s\n' 'Acquire::Retries "5";' 'Acquire::http::Timeout "120";' 'Acquire::https::Timeout "120";' 'Acquire::http::Pipeline-Depth "0";' > /etc/apt/apt.conf.d/99jayint-retries

# Install git for cloning
RUN command -v git >/dev/null 2>&1 || (apt-get update && apt-get install -y git)
```

**DA base image (dataabc__weibo-crawler.json, line 4):**
```
"dockerfile": "FROM python:3.12.0-alpine\n...
```

**Docker build error (run.log lines 644–646):**
```
#6 0.263 /bin/sh: can't create /etc/apt/apt.conf.d/99jayint-retries: nonexistent directory
#6 ERROR: process "/bin/sh -c printf '%s\\n' ..." did not complete successfully: exit code: 1
```

**Source of bad Dockerfile (src/synthesizer.py lines 3844–3859):**
```python
apt_bootstrap_instructions = build_dockerfile_apt_bootstrap_run_instructions()
...
if apt_bootstrap_instructions:
    content.extend(apt_bootstrap_instructions)  # Always added, no image type check
```

## Fix recommendation (for our agent.py / src/synthesizer.py / src/recipe_repair.py)

**Primary fix in src/synthesizer.py (line 3857–3859):**

Modify the Dockerfile generation to check the base image before applying apt bootstrap. Add a conditional guard:

```python
def generate_dockerfile(self, file_path="Dockerfile"):
    """Generates the final Dockerfile."""
    apt_bootstrap_instructions = build_dockerfile_apt_bootstrap_run_instructions()
    pip_bootstrap_instructions = build_dockerfile_pip_bootstrap_env_instructions()
    content = []
    if any("<<" in instruction for instruction in self.instructions):
        content.append("# syntax=docker/dockerfile:1")
    content.extend([
        f"FROM {self.base_image}",
        f"WORKDIR {self.workdir}",
        ""
    ])
    if pip_bootstrap_instructions:
        content.extend(pip_bootstrap_instructions)
        content.append("")
    
    # Only apply apt bootstrap if base image is Debian/Ubuntu-based
    if apt_bootstrap_instructions and not self._is_alpine_based(self.base_image):
        content.extend(apt_bootstrap_instructions)
        content.append("")
    
    content.extend(self._render_instruction_for_dockerfile(instruction) for instruction in self.instructions)
    ...
```

**Add image detection method:**

```python
def _is_alpine_based(self, base_image: str) -> bool:
    """Check if base image is Alpine Linux."""
    return 'alpine' in base_image.lower()
```

**Alternative:** If generating for Alpine, apply `apk` bootstrap instead via a separate function `build_dockerfile_apk_bootstrap_run_instructions()` that uses `apk add` with proper directory creation.

**Secondary fix:** Ensure all RUN statements in the generated Dockerfile use proper `&&` continuation or are properly separated, never leaving trailing backslashes without continuation.
