#!/bin/sh
# Copyright (C) 2026 花吹雪また一年P
# SPDX-License-Identifier: AGPL-3.0-or-later
# Linux build of the native core
ARCH=""
case "$(uname -m)" in
  x86_64)
    if grep -qw avx2 /proc/cpuinfo && grep -qw fma /proc/cpuinfo && grep -qw bmi2 /proc/cpuinfo; then
      ARCH="-march=x86-64-v3"
    else
      ARCH="-march=x86-64-v2"
    fi ;;
esac
cd "$(dirname "$0")" && g++ -O3 $ARCH -ffp-contract=off -std=c++17 -Wno-unused-function -Wno-misleading-indentation -shared -fPIC -pthread -o libgkmscore.so api.cpp
