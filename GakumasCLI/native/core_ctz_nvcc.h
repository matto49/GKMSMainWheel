// Copyright (C) 2026 花吹雪また一年P
// SPDX-License-Identifier: AGPL-3.0-or-later
// native exam core -- GK_CTZ64 for NVRTC and nvcc.
//   core_exam.h(292): error: calling a __device__ function("__ffsll") from a __host__ __device__
//   function("select_triggers") is not allowed
// Under nvcc a GK_FN is __host__ __device__ and __ffsll is device-only, so the host pass needs its own lowest-set-bit;
// NVRTC compiles the device side only and keeps __ffsll.  Included by core_base.h for CUDA compilers only: the CPU
// builds (cl, g++) do not read this file, and core_base.h keeps its line count (UNSUP reports __LINE__).
#pragma once
#if defined(__CUDACC_RTC__)
#define GK_CTZ64(x) (__ffsll((long long)(x)) - 1)
#else
#if defined(_MSC_VER) && !defined(__CUDA_ARCH__)
#include <intrin.h>
#endif
static inline __host__ __device__ int gk_ctz64(unsigned long long x) {
#if defined(__CUDA_ARCH__)
    return __ffsll((long long)x) - 1;
#elif defined(_MSC_VER)
    unsigned long i; _BitScanForward64(&i, x); return (int)i;
#else
    return __builtin_ctzll(x);
#endif
}
#define GK_CTZ64(x) gk_ctz64(x)
#endif
