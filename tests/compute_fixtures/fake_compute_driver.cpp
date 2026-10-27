#include "CL/cl.h"
#include "cuda.h"

#include <algorithm>
#include <cstring>
#include <functional>
#include <memory>
#include <vector>

struct FakeBuffer {
    std::vector<unsigned char> bytes;
    bool parameter = false;
};

struct FakeKernel {
    FakeBuffer* input = nullptr;
    FakeBuffer* output = nullptr;
};

namespace {
struct DriverState {
    std::vector<std::unique_ptr<FakeBuffer>> buffers;
    std::vector<std::unique_ptr<FakeKernel>> kernels;
    std::vector<std::function<void()>> commands;
    bool failParameterWrite = false;
    int finishFailureCountdown = 0;
    bool dropSourceWrite = false;
    int uploads = 0;
    int finishes = 0;
    int pendingWrites = 0;
    int releasedWithPendingWrites = 0;
};

DriverState state;

FakeBuffer* allocateBuffer(std::size_t bytes)
{
    auto buffer = std::make_unique<FakeBuffer>();
    buffer->bytes.resize(bytes);
    buffer->parameter = bytes == 10 * sizeof(double);
    auto* result = buffer.get();
    state.buffers.push_back(std::move(buffer));
    return result;
}

FakeKernel* allocateKernel()
{
    auto kernel = std::make_unique<FakeKernel>();
    auto* result = kernel.get();
    state.kernels.push_back(std::move(kernel));
    return result;
}

bool finish()
{
    ++state.finishes;
    const bool fail = state.finishFailureCountdown > 0 && --state.finishFailureCountdown == 0;
    state.dropSourceWrite = fail;
    for (const auto& command : state.commands) {
        command();
    }
    state.dropSourceWrite = false;
    state.commands.clear();
    state.pendingWrites = 0;
    return !fail;
}

bool rejectParameterWrite(const FakeBuffer* buffer)
{
    if (!buffer->parameter) {
        ++state.uploads;
        return false;
    }
    return state.failParameterWrite;
}
} // namespace

extern "C" {
void licasaFakeComputeReset() { state = DriverState{}; }
void licasaFakeComputeFailParameterWrite(int value) { state.failParameterWrite = value != 0; }
void licasaFakeComputeFailFinishAfter(int count) { state.finishFailureCountdown = count; }
int licasaFakeComputeUploads() { return state.uploads; }
int licasaFakeComputeFinishes() { return state.finishes; }
int licasaFakeComputePendingWrites() { return state.pendingWrites; }
int licasaFakeComputeUnsafeReleases() { return state.releasedWithPendingWrites; }

cl_int clGetPlatformIDs(cl_uint count, cl_platform_id* platforms, cl_uint* available)
{
    if (available) {
        *available = 1;
    }
    if (count && platforms) {
        *platforms = reinterpret_cast<cl_platform_id>(1);
    }
    return CL_SUCCESS;
}

cl_int clGetDeviceIDs(cl_platform_id, cl_device_type, cl_uint count, cl_device_id* devices,
                      cl_uint* available)
{
    if (available) {
        *available = 1;
    }
    if (count && devices) {
        *devices = reinterpret_cast<cl_device_id>(1);
    }
    return CL_SUCCESS;
}

cl_int clGetDeviceInfo(cl_device_id, cl_device_info info, std::size_t size, void* output,
                       std::size_t*)
{
    if (info == CL_DEVICE_DOUBLE_FP_CONFIG && size >= sizeof(cl_device_fp_config)) {
        *static_cast<cl_device_fp_config*>(output) = 1;
    } else if (info == CL_DEVICE_NAME) {
        const char name[] = "Deterministic fake compute device";
        std::memcpy(output, name, std::min(size, sizeof(name)));
    }
    return CL_SUCCESS;
}

cl_context clCreateContext(const std::intptr_t*, cl_uint, const cl_device_id*,
                           void (*)(const char*, const void*, std::size_t, void*), void*, cl_int*)
{
    return reinterpret_cast<cl_context>(1);
}

cl_command_queue clCreateCommandQueue(cl_context, cl_device_id, std::uint64_t, cl_int*)
{
    return reinterpret_cast<cl_command_queue>(1);
}

cl_program clCreateProgramWithSource(cl_context, cl_uint, const char**, const std::size_t*, cl_int*)
{
    return reinterpret_cast<cl_program>(1);
}

cl_int clBuildProgram(cl_program, cl_uint, const cl_device_id*, const char*,
                      void (*)(cl_program, void*), void*)
{
    return CL_SUCCESS;
}

cl_int clGetProgramBuildInfo(cl_program, cl_device_id, cl_program_build_info, std::size_t, void*,
                             std::size_t*)
{
    return CL_SUCCESS;
}

cl_kernel clCreateKernel(cl_program, const char*, cl_int*) { return allocateKernel(); }

cl_mem clCreateBuffer(cl_context, cl_mem_flags flags, std::size_t size, void* data, cl_int*)
{
    auto* buffer = allocateBuffer(size);
    if (flags & CL_MEM_COPY_HOST_PTR) {
        std::memcpy(buffer->bytes.data(), data, size);
        ++state.uploads;
    }
    return buffer;
}

cl_int clSetKernelArg(cl_kernel kernel, cl_uint index, std::size_t, const void* value)
{
    if (index == 0) {
        kernel->input = *static_cast<cl_mem const*>(value);
    } else if (index == 1) {
        kernel->output = *static_cast<cl_mem const*>(value);
    }
    return CL_SUCCESS;
}

cl_int clEnqueueWriteBuffer(cl_command_queue, cl_mem buffer, cl_bool blocking, std::size_t offset,
                            std::size_t size, const void* data, cl_uint, const cl_event*, cl_event*)
{
    if (rejectParameterWrite(buffer)) {
        return -1;
    }
    ++state.pendingWrites;
    // Retain the borrowed host pointer until the real backend fences the queue.
    state.commands.push_back([=] {
        if (!state.dropSourceWrite || buffer->parameter) {
            std::memcpy(buffer->bytes.data() + offset, data, size);
        }
    });
    if (blocking) {
        return finish() ? CL_SUCCESS : -1;
    }
    return CL_SUCCESS;
}

cl_int clEnqueueNDRangeKernel(cl_command_queue, cl_kernel kernel, cl_uint, const std::size_t*,
                              const std::size_t*, const std::size_t*, cl_uint, const cl_event*,
                              cl_event*)
{
    auto* input = kernel->input;
    auto* output = kernel->output;
    state.commands.push_back([=] { output->bytes = input->bytes; });
    return CL_SUCCESS;
}

cl_int clEnqueueReadBuffer(cl_command_queue, cl_mem buffer, cl_bool, std::size_t offset,
                           std::size_t size, void* output, cl_uint, const cl_event*, cl_event*)
{
    if (!finish()) {
        return -1;
    }
    std::memcpy(output, buffer->bytes.data() + offset, size);
    return CL_SUCCESS;
}

cl_int clFinish(cl_command_queue) { return finish() ? CL_SUCCESS : -1; }

cl_int clReleaseMemObject(cl_mem)
{
    if (state.pendingWrites) {
        ++state.releasedWithPendingWrites;
    }
    // Keep fixture storage until reset so an old backend fails assertions
    // without turning its deliberate missing-fence negative control into UB.
    return CL_SUCCESS;
}

cl_int clReleaseKernel(cl_kernel) { return CL_SUCCESS; }
cl_int clReleaseProgram(cl_program) { return CL_SUCCESS; }
cl_int clReleaseCommandQueue(cl_command_queue) { return CL_SUCCESS; }
cl_int clReleaseContext(cl_context) { return CL_SUCCESS; }

CUresult cuInit(unsigned int) { return CUDA_SUCCESS; }
CUresult cuDeviceGet(CUdevice* device, int)
{
    *device = 0;
    return CUDA_SUCCESS;
}
CUresult cuDeviceGetName(char* output, int size, CUdevice)
{
    const char name[] = "Deterministic fake compute device";
    std::memcpy(output, name, std::min(std::size_t(size), sizeof(name)));
    return CUDA_SUCCESS;
}
CUresult cuCtxCreate_v2(CUcontext* context, unsigned int, CUdevice)
{
    *context = reinterpret_cast<CUcontext>(1);
    return CUDA_SUCCESS;
}
CUresult cuCtxDestroy(CUcontext) { return CUDA_SUCCESS; }
CUresult cuCtxPushCurrent(CUcontext) { return CUDA_SUCCESS; }
CUresult cuCtxPopCurrent(CUcontext* context)
{
    *context = nullptr;
    return CUDA_SUCCESS;
}
CUresult cuCtxSynchronize()
{
    ++state.finishes;
    return state.finishFailureCountdown > 0 && --state.finishFailureCountdown == 0 ? -1
                                                                                   : CUDA_SUCCESS;
}
CUresult cuModuleLoadData(CUmodule* module, const void*)
{
    *module = reinterpret_cast<CUmodule>(1);
    return CUDA_SUCCESS;
}
CUresult cuModuleGetFunction(CUfunction* function, CUmodule, const char*)
{
    *function = allocateKernel();
    return CUDA_SUCCESS;
}
CUresult cuModuleUnload(CUmodule) { return CUDA_SUCCESS; }
CUresult cuMemAlloc(CUdeviceptr* pointer, std::size_t bytes)
{
    *pointer = reinterpret_cast<CUdeviceptr>(allocateBuffer(bytes));
    return CUDA_SUCCESS;
}
CUresult cuMemFree(CUdeviceptr) { return CUDA_SUCCESS; }
CUresult cuMemcpyHtoD(CUdeviceptr pointer, const void* source, std::size_t bytes)
{
    auto* buffer = reinterpret_cast<FakeBuffer*>(pointer);
    if (rejectParameterWrite(buffer)) {
        return -1;
    }
    std::memcpy(buffer->bytes.data(), source, bytes);
    return CUDA_SUCCESS;
}
CUresult cuMemcpyDtoH(void* output, CUdeviceptr pointer, std::size_t bytes)
{
    const auto* buffer = reinterpret_cast<FakeBuffer*>(pointer);
    std::memcpy(output, buffer->bytes.data(), bytes);
    return CUDA_SUCCESS;
}
CUresult cuLaunchKernel(CUfunction, unsigned int, unsigned int, unsigned int, unsigned int,
                        unsigned int, unsigned int, unsigned int, CUstream, void** arguments,
                        void**)
{
    const auto* input = reinterpret_cast<FakeBuffer*>(*static_cast<CUdeviceptr*>(arguments[0]));
    auto* output = reinterpret_cast<FakeBuffer*>(*static_cast<CUdeviceptr*>(arguments[1]));
    output->bytes = input->bytes;
    return CUDA_SUCCESS;
}
}
