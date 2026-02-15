FROM python:3.10
WORKDIR /app

RUN ls -la
RUN cat pyproject.toml
RUN pip install pyyaml requests jinja2 "pydantic>=2.0" "litellm>=1.75.5" tenacity rich python-dotenv typer platformdirs textual prompt_toolkit datasets "openai!=1.100.0,!=1.100.1"
RUN cat README.md