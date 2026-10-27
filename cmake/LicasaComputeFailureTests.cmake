# Compile the real driver adapters against deterministic, test-only interfaces.
# These fixtures exercise upload ordering and failure cleanup without GPU SDKs.
# They are never linked into, searched by, or installed with the application.
foreach(backend IN ITEMS OpenCL cuda)
    add_library(licasa_fake_${backend} SHARED tests/compute_fixtures/fake_compute_driver.cpp)
    target_include_directories(licasa_fake_${backend} PRIVATE tests/compute_fixtures)
    set_target_properties(licasa_fake_${backend} PROPERTIES
            OUTPUT_NAME "${backend}" SOVERSION 1
            LIBRARY_OUTPUT_DIRECTORY "${CMAKE_CURRENT_BINARY_DIR}/fake-compute")
    licasa_enable_warnings(licasa_fake_${backend})
endforeach()

add_executable(licasa_edit_compute_failure_tests
        tests/tst_edit_compute_failures.cpp
        src/imaging/compute/opencl_edit_device.cpp
        src/imaging/compute/cuda_edit_device.cpp)
target_include_directories(licasa_edit_compute_failure_tests PRIVATE tests/compute_fixtures src)
target_link_libraries(licasa_edit_compute_failure_tests PRIVATE Qt6::Core Qt6::Gui Qt6::Test)
add_dependencies(licasa_edit_compute_failure_tests licasa_fake_OpenCL licasa_fake_cuda)
licasa_enable_warnings(licasa_edit_compute_failure_tests)
list(APPEND LICASA_TEST_TARGETS licasa_edit_compute_failure_tests)
list(APPEND LICASA_CORE_TEST_TARGETS licasa_edit_compute_failure_tests)
add_test(NAME licasa_edit_compute_failure_tests COMMAND licasa_edit_compute_failure_tests)
set_tests_properties(licasa_edit_compute_failure_tests PROPERTIES
        TIMEOUT 15 LABELS "core;editing;lifecycle;fast"
        ENVIRONMENT "LD_LIBRARY_PATH=$<TARGET_FILE_DIR:licasa_fake_OpenCL>")
