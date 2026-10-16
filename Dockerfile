FROM mambaorg/micromamba:1.5-jammy
RUN micromamba install -y -n base -c conda-forge python=3.11 cadquery && micromamba clean -a -y
ARG MAMBA_DOCKERFILE_ACTIVATE=1
RUN pip install --no-cache-dir "mcp[cli]<2"
WORKDIR /app
COPY server.py json_builder.py ./
ENV D2C_OUT=/data/out
# Basis-Image laeuft als mambauser; /data/out muss ihm gehoeren, sonst bricht server.py beim Start ab
USER root
RUN mkdir -p /data/out && chown -R $MAMBA_USER:$MAMBA_USER /data
USER $MAMBA_USER
# Start ohne Netzwerk:  docker run -i --rm --network none -v $PWD/out:/data/out drawing2cad
# Linux-Host: zusaetzlich --user $(id -u):$(id -g), damit in das gemountete out/ geschrieben werden darf
CMD ["python", "server.py"]
