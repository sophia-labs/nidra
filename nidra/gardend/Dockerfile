# The Sophia engine (gardend) release image plus ONNX Runtime, so a standalone
# Nidra builds its own `find` index (multilingual-e5-small, embedded locally).
# gardend pins `ort` at API 24, so ONNX Runtime 1.23 or newer; the release
# image is FROM scratch with libstdc++, libgomp and libgcc_s present.
ARG GARDEND_IMAGE=816069160218.dkr.ecr.us-west-1.amazonaws.com/gardend-release:src-5870c0029629-ship
ARG ORT_VERSION=1.28.3

FROM alpine:3.20 AS ort
ARG ORT_VERSION
RUN apk add --no-cache curl tar \
 && curl -sSL -o /tmp/ort.tgz "https://github.com/microsoft/onnxruntime/releases/download/v${ORT_VERSION}/onnxruntime-linux-x64-${ORT_VERSION}.tgz" \
 && mkdir -p /ort && tar -xzf /tmp/ort.tgz -C /ort --strip-components=1 \
 && cp /ort/lib/libonnxruntime.so.${ORT_VERSION} /libonnxruntime.so && cp /ort/LICENSE /ONNXRUNTIME-LICENSE

FROM ${GARDEND_IMAGE}
COPY --from=ort /libonnxruntime.so /usr/local/lib/libonnxruntime.so
COPY --from=ort /ONNXRUNTIME-LICENSE /usr/local/share/licenses/ONNXRUNTIME-LICENSE
ENV ORT_DYLIB_PATH=/usr/local/lib/libonnxruntime.so
