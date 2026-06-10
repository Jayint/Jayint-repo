FROM python:3.10
WORKDIR /
RUN apt-get update && apt-get install -y curl
RUN curl -sSL https://install.python-poetry.org | python -
ENV PATH="/root/.local/bin:$PATH"
RUN pip install pytest pytest-xdist
RUN pip install pipdeptree
RUN git clone https://github.com/docling-project/docling.git
RUN mkdir /repo
RUN git config --global --add safe.directory /repo
RUN cp -r /docling/. /repo && rm -rf /docling/
RUN rm -rf /docling
RUN cd /repo && find . -name "requirements*.txt" -type f 2>/dev/null; find . -name "requirements*.in" -type f 2>/dev/null; find . -name "Pipfile" -type f 2>/dev/null; find . -name "environment.yml" -type f 2>/dev/null
RUN pip install arelle==2.2
RUN pip install beautifulsoup4==4.14.3
RUN pip install click==8.4.1
RUN pip install defusedxml==0.7.1
RUN pip install docling_core==2.79.0
RUN pip install docling_ibm_models==3.13.3
RUN pip install docling_parse==6.2.0
RUN pip install easyocr==1.7.2
RUN pip install filetype==1.2.0
RUN pip install gliner==0.2.26
RUN pip install httpx==0.28.1
RUN pip install huggingface_hub==1.18.0
RUN pip install jupytext==1.19.3
RUN pip install lxml==6.1.1
RUN pip install marko==2.2.3
RUN pip install matplotlib==3.10.9
RUN pip install mlx_vlm==0.6.2
RUN pip install mlx_whisper==0.4.3
RUN pip install modelscope==1.37.1
RUN pip install nbconvert==7.17.1
RUN pip install openpyxl==3.1.4
RUN pip install packaging==26.2
RUN pip install Pillow==12.2.0
RUN pip install playwright==1.60.0
RUN pip install pluggy==1.6.0
RUN pip install polyfactory==3.3.0
RUN pip install psutil==7.2.2
RUN pip install pyarrow==24.0.0
RUN pip install pydantic==2.13.4
RUN pip install pydantic_core==2.46.4
RUN pip install pydantic_settings==2.14.1
RUN pip install pylatexenc==2.10
RUN pip install pypdfium2==5.9.0
RUN pip install pytest==9.0.3
RUN pip install python-dotenv==1.2.2
RUN pip install PyYAML==6.0.3
RUN pip install qwen_vl_utils==0.0.14
RUN pip install rapidocr==3.8.1
RUN pip install rich==15.0.0
RUN pip install rtree==1.4.1
RUN pip install tabulate==0.10.0
RUN pip install tesserocr==2.10.0
RUN pip install tomli==2.4.1
RUN pip install torch==2.11.0
RUN pip install torchvision==0.26.0
RUN pip install tqdm==4.67.3
RUN pip install transformers==5.10.2
RUN pip install tritonclient==2.69.0
RUN pip install typer==0.24.2
RUN pip install typing_extensions==4.15.0
RUN pip install vllm==0.22.1
RUN pip install websockets==16.0
RUN pip install whisper==1.1.10
RUN cd /repo && pip install -q -e . 2>&1 | tail -20
RUN pip install -q mail-parser==4.3.0
RUN pip install -q numpy>=1.24,<2 pandas>=2.1,<3 scipy>=1.6,<2 onnxruntime>=1.7,<2 grpcio>=1.0 2>&1 | tail -10
RUN pip install -q python-pptx==1.0.2 python-docx==1.2.0