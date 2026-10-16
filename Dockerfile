FROM mambaorg/micromamba:1.5-jammy
RUN micromamba install -y -n base -c conda-forge python=3.11 cadquery && micromamba clean -a -y
ARG MAMBA_DOCKERFILE_ACTIVATE=1
RUN pip install --no-cache-dir "mcp[cli]"
WORKDIR /app
COPY server.py json_builder.py ./
ENV D2C_OUT=/data/out
# Start ohne Netzwerk:  docker run -i --rm --network none -v $PWD/out:/data/out drawing2cad
CMD ["python", "server.py"]
