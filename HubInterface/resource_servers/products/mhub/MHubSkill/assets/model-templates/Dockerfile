FROM mhubai/base:latest

WORKDIR /app/model
COPY . /app/model

RUN uv pip install -e .

# Entrypoint matches mhub.io Docker guidance (python3 -m mhubio.run)
ENTRYPOINT ["python3", "-m", "mhubio.run"]
CMD ["--workflow", "default"]
