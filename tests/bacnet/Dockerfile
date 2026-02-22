FROM python:3.11-slim-bookworm
RUN pip install --no-cache-dir BAC0>=24.0.0
COPY test_bacnet_protocol.py /test_bacnet_protocol.py
ENTRYPOINT ["python", "/test_bacnet_protocol.py"]
