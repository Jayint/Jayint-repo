"""Unit tests for the ported ImageSelector — fake LLM client, no network."""
import os
import tempfile
import types

from src.image_selector import ImageSelector


class _FakeMessage:
    def __init__(self, content):
        self.content = content


class _FakeChoice:
    def __init__(self, content):
        self.message = _FakeMessage(content)


class _FakeResponse:
    def __init__(self, content):
        self.choices = [_FakeChoice(content)]
        self.usage = types.SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2)


class _FakeCompletions:
    """Serves scripted LLM responses keyed to *stage*, not raw call count.

    ImageSelector calls create() a variable number of times per stage: locate=1,
    relevance=1-per-candidate-file, detect_language=0-or-1 (skipped when a
    language_hint is given), select_image=1-plus-retries. Routing on a snippet
    unique to each real prompt template lets one scripted response serve every
    call within a stage (e.g. each per-file relevance check gets the same
    verdict) while distinct stages still consume distinct script entries, in
    script order — regardless of how many files/retries actually occur.
    """

    _STAGE_MARKERS = (
        ("locate", "most relevant files for setting up"),
        ("relevance", "determine if it is relevant"),
        ("detect_language", "PRIMARY programming language"),
        ("select_image", "recommend a suitable base Docker image"),
    )

    def __init__(self, script):
        self._script = list(script)
        self.calls = []
        self._stage_slot = {}
        self._next_slot = 0

    def create(self, model, messages, temperature=0, **kw):
        self.calls.append(messages)
        prompt = messages[0]["content"]
        stage = next((name for name, marker in self._STAGE_MARKERS if marker in prompt), None)
        if stage not in self._stage_slot:
            self._stage_slot[stage] = min(self._next_slot, len(self._script) - 1)
            self._next_slot += 1
        content = self._script[self._stage_slot[stage]]
        return _FakeResponse(content)


class _FakeClient:
    def __init__(self, script):
        self.chat = types.SimpleNamespace(completions=_FakeCompletions(script))


def _repo_with(files: dict) -> str:
    d = tempfile.mkdtemp()
    for rel, content in files.items():
        path = os.path.join(d, rel)
        os.makedirs(os.path.dirname(path) or d, exist_ok=True)
        with open(path, "w") as fh:
            fh.write(content)
    return d


def test_select_base_image_uses_llm_choice_from_candidates():
    repo = _repo_with({
        "pyproject.toml": "[project]\nname='x'\nrequires-python='>=3.10'\n",
        "README.md": "# demo python project",
    })
    # Scripted LLM turns: locate-files, (relevance per file), detect-language, select-image.
    # PythonHandler.base_images() yields plain "python:3.N" tags (no "-slim" suffix), so
    # the scripted <image> choice must be one of those to pass the candidate-membership check.
    client = _FakeClient([
        "<file>pyproject.toml</file>\n<file>README.md</file>",  # locate
        "<rel>Yes</rel>",                                        # relevance (repeated per file)
        "<lang>python</lang>",                                   # detect language
        "<image>python:3.10</image>",                            # select image
    ])
    sel = ImageSelector(client, model="fake-model")
    image, handler, docs, platform_override = sel.select_base_image(repo)
    assert image == "python:3.10"
    assert handler is not None
    assert isinstance(docs, str)


def test_language_detection_falls_back_to_rules_when_llm_returns_junk():
    repo = _repo_with({"requirements.txt": "requests\n", "app.py": "print('hi')\n"})
    # detect-language turn returns no <lang> tag -> rule-based detect_language runs on the
    # real repo structure (a requirements.txt + .py repo resolves to 'python').
    client = _FakeClient([
        "<file>requirements.txt</file>\n<file>app.py</file>",  # locate
        "<rel>Yes</rel>",                                       # relevance
        "I am not sure",                                        # detect-language junk -> None
        "<image>python:3.11</image>",                           # select image
    ])
    sel = ImageSelector(client, model="fake-model")
    image, handler, _docs, _po = sel.select_base_image(repo)
    assert "python" in image  # a python candidate was chosen


def test_language_hint_short_circuits_llm_detection():
    repo = _repo_with({"main.py": "x = 1\n"})
    client = _FakeClient([
        "<file>main.py</file>",             # locate
        "<rel>Yes</rel>",                   # relevance
        "<image>python:3.12</image>",       # select image (no detect-language call needed)
    ])
    sel = ImageSelector(client, model="fake-model")
    image, handler, _docs, _po = sel.select_base_image(repo, language_hint="python")
    assert image == "python:3.12"
