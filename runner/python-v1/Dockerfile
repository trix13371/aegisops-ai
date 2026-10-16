FROM python@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1
COPY requirements.txt data-requirements.txt /opt/aegisops/
ARG DEPENDENCIES=requirements.txt
RUN python -m pip install --no-cache-dir --only-binary=:all: --require-hashes -r /opt/aegisops/${DEPENDENCIES}
COPY entry.py /opt/aegisops/entry.py
ENV PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 NO_COLOR=1 HOME=/tmp
USER 65532:65532
WORKDIR /work
ENTRYPOINT ["python", "-I", "/opt/aegisops/entry.py"]
