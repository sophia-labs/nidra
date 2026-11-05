# The Sophia engine (gardend) for a standalone Nidra: the release binary on a
# Debian base (the FROM-scratch release image hangs gardend's embedder; the same
# binary on Debian embeds 274 entities in 6 s), plus what `find` needs offline:
# ONNX Runtime (gardend pins `ort` at API 24, so 1.23 or newer) and the
# multilingual-e5-small model, baked in so nothing is downloaded at run time.
# The engine writes the catalog through this gardend's Emporium ledger (NIDRA_WRITES=commit, the default), so
# the release image must be a build with the ledger (the /emporium/commit route).
# TODO(lead): ledger build tag (the release image of gardend main at the W7d ledger, 7968ea6 or later)
ARG GARDEND_IMAGE=816069160218.dkr.ecr.us-west-1.amazonaws.com/gardend-release:src-5870c0029629-ship
ARG ORT_VERSION=1.28.3
ARG E5_REVISION=614241f622f53c4eeff9890bdc4f31cfecc418b3

FROM alpine:3.20 AS deps
ARG ORT_VERSION
ARG E5_REVISION
RUN apk add --no-cache curl tar \
 && curl -sSL -o /tmp/ort.tgz "https://github.com/microsoft/onnxruntime/releases/download/v${ORT_VERSION}/onnxruntime-linux-x64-${ORT_VERSION}.tgz" \
 && mkdir -p /ort && tar -xzf /tmp/ort.tgz -C /ort --strip-components=1 \
 && cp /ort/lib/libonnxruntime.so.${ORT_VERSION} /libonnxruntime.so && cp /ort/LICENSE /ONNXRUNTIME-LICENSE
# hf-hub cache layout: refs/main names the revision, snapshots/<rev>/<file> holds the files.
RUN M=/fastembed/models--intfloat--multilingual-e5-small && S=$M/snapshots/${E5_REVISION} \
 && mkdir -p $M/refs $S/onnx && printf '%s' "${E5_REVISION}" > $M/refs/main \
 && for f in onnx/model.onnx tokenizer.json config.json special_tokens_map.json tokenizer_config.json; do \
      curl -sSL -o "$S/$f" "https://huggingface.co/intfloat/multilingual-e5-small/resolve/${E5_REVISION}/$f"; done \
 && curl -sSL -o /E5-README.md "https://huggingface.co/intfloat/multilingual-e5-small/resolve/${E5_REVISION}/README.md"

FROM ${GARDEND_IMAGE} AS release

FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl libstdc++6 libgomp1 \
 && rm -rf /var/lib/apt/lists/*
COPY --from=release /usr/local/bin/gardend /usr/local/bin/gardend
COPY --from=release /usr/local/lib/gardend /usr/local/lib/gardend
COPY --from=release /usr/local/share/gardend /usr/local/share/gardend
COPY --from=deps /libonnxruntime.so /usr/local/lib/libonnxruntime.so
COPY --from=deps /ONNXRUNTIME-LICENSE /usr/local/share/licenses/ONNXRUNTIME-LICENSE
COPY --from=deps /fastembed /opt/fastembed
COPY --from=deps /E5-README.md /usr/local/share/licenses/MULTILINGUAL-E5-SMALL-README.md
COPY entrypoint.sh /usr/local/bin/nidra-gardend
ENV GARDEN_PROFILE_DIR=/data/profile \
    GARDEN_PDFIUM_LIB=/usr/local/lib/gardend/libpdfium.so \
    GARDEN_LOOPBACK_HOST=0.0.0.0 \
    GARDEN_LOOPBACK_PORT=8086 \
    GARDEN_LOCAL_CRDT_OPERATION_TIMEOUT_SECS=1800 \
    GARDEN_NO_UPDATE_CHECK=1 \
    HOME=/tmp \
    RUST_LOG=info \
    ORT_DYLIB_PATH=/usr/local/lib/libonnxruntime.so \
    FASTEMBED_CACHE_DIR=/opt/fastembed
EXPOSE 8086
VOLUME /data
ENTRYPOINT ["/usr/local/bin/nidra-gardend"]
