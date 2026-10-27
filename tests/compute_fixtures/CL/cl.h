#pragma once

// Test-only interface for compiling the production OpenCL driver without an SDK.
// The fixture models transfer ordering and failure, not GPU kernel arithmetic.
#include <cstddef>
#include <cstdint>

using cl_int = std::int32_t;
using cl_uint = std::uint32_t;
using cl_device_type = std::uint64_t;
using cl_device_fp_config = std::uint64_t;
using cl_mem_flags = std::uint64_t;
using cl_bool = cl_uint;
using cl_device_info = cl_uint;
using cl_program_build_info = cl_uint;
struct FakePlatform;
struct FakeDevice;
struct FakeContext;
struct FakeQueue;
struct FakeProgram;
struct FakeKernel;
struct FakeBuffer;
using cl_platform_id = FakePlatform*;
using cl_device_id = FakeDevice*;
using cl_context = FakeContext*;
using cl_command_queue = FakeQueue*;
using cl_program = FakeProgram*;
using cl_kernel = FakeKernel*;
using cl_mem = FakeBuffer*;
using cl_event = void*;

inline constexpr cl_int CL_SUCCESS = 0;
inline constexpr cl_bool CL_FALSE = 0;
inline constexpr cl_bool CL_TRUE = 1;
inline constexpr cl_device_type CL_DEVICE_TYPE_GPU = 4;
inline constexpr cl_device_info CL_DEVICE_DOUBLE_FP_CONFIG = 0x1032;
inline constexpr cl_device_info CL_DEVICE_NAME = 0x102B;
inline constexpr cl_program_build_info CL_PROGRAM_BUILD_LOG = 0x1183;
inline constexpr cl_mem_flags CL_MEM_READ_WRITE = 1;
inline constexpr cl_mem_flags CL_MEM_READ_ONLY = 4;
inline constexpr cl_mem_flags CL_MEM_COPY_HOST_PTR = 32;

extern "C" {
cl_int clGetPlatformIDs(cl_uint, cl_platform_id*, cl_uint*);
cl_int clGetDeviceIDs(cl_platform_id, cl_device_type, cl_uint, cl_device_id*, cl_uint*);
cl_int clGetDeviceInfo(cl_device_id, cl_device_info, std::size_t, void*, std::size_t*);
cl_context clCreateContext(const std::intptr_t*, cl_uint, const cl_device_id*,
                           void (*)(const char*, const void*, std::size_t, void*), void*, cl_int*);
cl_command_queue clCreateCommandQueue(cl_context, cl_device_id, std::uint64_t, cl_int*);
cl_program clCreateProgramWithSource(cl_context, cl_uint, const char**, const std::size_t*,
                                     cl_int*);
cl_int clBuildProgram(cl_program, cl_uint, const cl_device_id*, const char*,
                      void (*)(cl_program, void*), void*);
cl_int clGetProgramBuildInfo(cl_program, cl_device_id, cl_program_build_info, std::size_t, void*,
                             std::size_t*);
cl_kernel clCreateKernel(cl_program, const char*, cl_int*);
cl_mem clCreateBuffer(cl_context, cl_mem_flags, std::size_t, void*, cl_int*);
cl_int clSetKernelArg(cl_kernel, cl_uint, std::size_t, const void*);
cl_int clEnqueueWriteBuffer(cl_command_queue, cl_mem, cl_bool, std::size_t, std::size_t,
                            const void*, cl_uint, const cl_event*, cl_event*);
cl_int clEnqueueNDRangeKernel(cl_command_queue, cl_kernel, cl_uint, const std::size_t*,
                              const std::size_t*, const std::size_t*, cl_uint, const cl_event*,
                              cl_event*);
cl_int clEnqueueReadBuffer(cl_command_queue, cl_mem, cl_bool, std::size_t, std::size_t, void*,
                           cl_uint, const cl_event*, cl_event*);
cl_int clFinish(cl_command_queue);
cl_int clReleaseMemObject(cl_mem);
cl_int clReleaseKernel(cl_kernel);
cl_int clReleaseProgram(cl_program);
cl_int clReleaseCommandQueue(cl_command_queue);
cl_int clReleaseContext(cl_context);
}
