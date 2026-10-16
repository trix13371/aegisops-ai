FROM node@sha256:7f7f17f773027e7f0475fa829fc0d793d75918fda85b9e517c9cc5aa46200406
COPY javascript-v1/package.json javascript-v1/package-lock.json /opt/aegisops/
RUN cd /opt/aegisops && npm ci --ignore-scripts --no-audit --no-fund \
    && python3 --version && ln -s /usr/bin/python3 /usr/local/bin/python
COPY python-v1/entry.py /opt/aegisops/python_runner.py
COPY javascript-v1/entry.py javascript-v1/check.mjs /opt/aegisops/
ENV NO_COLOR=1 HOME=/tmp
USER 65532:65532
WORKDIR /work
ENTRYPOINT ["python", "-I", "/opt/aegisops/entry.py"]
CMD []
