#!/usr/bin/env bash
set -euo pipefail
# Call inside cross compiler container with /src:ro, /repo:ro and /out:rw.
# Usage: build.sh DEFCONFIG LOCALVERSION FRAG1 [FRAG2 ...]
defconfig="$1"; localversion="$2"; shift 2
export ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu-
mkdir -p /out/obj /out/stage /out/dist
make -s -C /src O=/out/obj "$defconfig"
printf 'CONFIG_LOCALVERSION="%s"\nCONFIG_LOCALVERSION_AUTO=n\n' "$localversion" > /out/localversion.cfg
# -m: merge without invoking make; olddefconfig below resolves dependencies.
bash /src/scripts/kconfig/merge_config.sh -m -O /out/obj \
  /out/obj/.config "$@" /out/localversion.cfg
make -s -C /src O=/out/obj olddefconfig
python3 /repo/docker/check_config.py /out/obj/.config "$@" /out/localversion.cfg
make -C /src O=/out/obj -j "${BUILD_JOBS:-$(nproc)}" Image modules dtbs
release="$(make -s -C /src O=/out/obj kernelrelease)"
[[ "$release" == *"$localversion" ]] || { echo 'kernelrelease missing localversion' >&2; exit 1; }
make -s -C /src O=/out/obj INSTALL_MOD_PATH=/out/stage modules_install
# Do not bundle generated build/source links or entire source trees.
find /out/stage/lib/modules -type l -name build -delete
find /out/stage/lib/modules -type l -name source -delete
cp /out/obj/arch/arm64/boot/Image /out/dist/kernel.img
cp /out/obj/.config /out/dist/config
printf '%s\n' "$release" > /out/dist/kernelrelease
(cd /out/stage && tar -czf /out/dist/modules.tar.gz lib/modules)
mkdir -p /out/dtbs
# Retain the DTS path hierarchy for later board-specific deployment.
(cd /out/obj/arch/arm64/boot/dts && find . \( -name '*.dtb' -o -name '*.dtbo' \) -type f -exec cp --parents '{}' /out/dtbs/ \;)
(cd /out/dtbs && tar -czf /out/dist/dtbs.tar.gz .)
