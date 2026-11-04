# The Sophia engine (gardend) release image plus ONNX Runtime, so a standalone
# Nidra can build its own `find` index (multilingual-e5-small, local). The
# release image is FROM scratch with libstdc++, libgomp and libgcc_s present.
ARG GARDEND_IMAGE=816069160218.dkr.ecr.us-west-1.amazonaws.com/gardend-release:src-5870c0029629-ship
FROM ${GARDEND_IMAGE}
COPY libonnxruntime.so /usr/local/lib/libonnxruntime.so
COPY ONNXRUNTIME-LICENSE /usr/local/share/licenses/ONNXRUNTIME-LICENSE
ENV ORT_DYLIB_PATH=/usr/local/lib/libonnxruntime.so
