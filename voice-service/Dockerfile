FROM python:3.11-slim

RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH \
    HF_HOME=/home/user/.cache/huggingface

WORKDIR $HOME/app

RUN pip install --no-cache-dir --upgrade pip

# torch specifically from the CPU-only wheel index -- Spaces' free CPU tier has no GPU,
# and the default PyPI package is a multi-GB CUDA build.
RUN pip install --no-cache-dir --user torch --index-url https://download.pytorch.org/whl/cpu

COPY --chown=user requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

COPY --chown=user . .

EXPOSE 8080

# $PORT is Cloud Run's injected convention (defaults to 8080 locally / if unset, which
# also happens to match Cloud Run's own default).
CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT:-8080}"]
