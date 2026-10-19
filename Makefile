# Native Bullet C++ build using Bullet's CMake configuration.
# This builds Bullet libraries for the current host; it does not build HashLink bindings.
#
# Usage:
#   make -f Makefile_native -j"$(nproc)"
#   make -f Makefile_native clean

ROOT := $(patsubst %/,%,$(abspath $(dir $(lastword $(MAKEFILE_LIST)))))
BUILD_DIR ?= $(ROOT)/../build-native-$(shell uname -m)
OUT_DIR ?= $(BUILD_DIR)/out
JOBS ?= $(shell nproc)
BUILD_TYPE ?= Release
CMAKE ?= cmake

.PHONY: all clean collect

all:
	$(CMAKE) -S "$(ROOT)" -B "$(BUILD_DIR)" \
		-DCMAKE_BUILD_TYPE=$(BUILD_TYPE) \
		-DBUILD_SHARED_LIBS=ON \
		-DBUILD_BULLET3=OFF \
		-DBUILD_BULLET2_DEMOS=OFF \
		-DBUILD_EXTRAS=OFF \
		-DBUILD_UNIT_TESTS=OFF \
		-DBUILD_PYBULLET=OFF \
		-DBUILD_EGL=OFF \
		-DINSTALL_LIBS=OFF
	$(CMAKE) --build "$(BUILD_DIR)" --parallel "$(JOBS)"
	$(MAKE) -f "$(lastword $(MAKEFILE_LIST))" collect

collect:
	mkdir -p "$(OUT_DIR)"
	find "$(BUILD_DIR)" -path "$(OUT_DIR)" -prune -o -name '*.so' -print -exec cp {} "$(OUT_DIR)/" \;

clean:
	rm -rf "$(BUILD_DIR)"
