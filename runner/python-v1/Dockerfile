FROM python@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1
COPY requirements.txt /opt/aegisops/requirements.txt
RUN python -m pip install --no-cache-dir --require-hashes -r /opt/aegisops/requirements.txt
COPY entry.py /opt/aegisops/entry.py
ENV PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 NO_COLOR=1 HOME=/tmp
USER 65532:65532
WORKDIR /work
ENTRYPOINT ["python", "-I", "/opt/aegisops/entry.py"]
