#pragma once

// Test-only CUDA interface; no SDK, driver, device or PTX compiler is required.
#include <cstddef>
#include <cstdint>

using CUresult = int;
using CUdevice = int;
using CUcontext = void*;
using CUmodule = void*;
struct FakeKernel;
using CUfunction = FakeKernel*;
using CUdeviceptr = std::uintptr_t;
using CUstream = void*;
inline constexpr CUresult CUDA_SUCCESS = 0;
inline constexpr unsigned int CU_CTX_SCHED_BLOCKING_SYNC = 4;

#define cuCtxDestroy cuCtxDestroy_v2
#define cuCtxPushCurrent cuCtxPushCurrent_v2
#define cuCtxPopCurrent cuCtxPopCurrent_v2
#define cuMemAlloc cuMemAlloc_v2
#define cuMemFree cuMemFree_v2
#define cuMemcpyHtoD cuMemcpyHtoD_v2
#define cuMemcpyDtoH cuMemcpyDtoH_v2

extern "C" {
CUresult cuInit(unsigned int);
CUresult cuDeviceGet(CUdevice*, int);
CUresult cuDeviceGetName(char*, int, CUdevice);
CUresult cuCtxCreate_v2(CUcontext*, unsigned int, CUdevice);
CUresult cuCtxDestroy(CUcontext);
CUresult cuCtxPushCurrent(CUcontext);
CUresult cuCtxPopCurrent(CUcontext*);
CUresult cuCtxSynchronize();
CUresult cuModuleLoadData(CUmodule*, const void*);
CUresult cuModuleGetFunction(CUfunction*, CUmodule, const char*);
CUresult cuModuleUnload(CUmodule);
CUresult cuMemAlloc(CUdeviceptr*, std::size_t);
CUresult cuMemFree(CUdeviceptr);
CUresult cuMemcpyHtoD(CUdeviceptr, const void*, std::size_t);
CUresult cuMemcpyDtoH(void*, CUdeviceptr, std::size_t);
CUresult cuLaunchKernel(CUfunction, unsigned int, unsigned int, unsigned int, unsigned int,
                        unsigned int, unsigned int, unsigned int, CUstream, void**, void**);
}
