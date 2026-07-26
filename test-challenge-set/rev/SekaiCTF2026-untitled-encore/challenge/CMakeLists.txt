cmake_minimum_required(VERSION 3.16)
project(untitled_encore_native LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

add_executable(untitled-encore-native
    app/encore_native.cpp
)

target_include_directories(untitled-encore-native PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}
    ${CMAKE_CURRENT_SOURCE_DIR}/generated
    ${CMAKE_CURRENT_SOURCE_DIR}/mmw4cc
)

if(MSVC)
    target_compile_options(untitled-encore-native PRIVATE /W4 /permissive- /EHsc)
else()
    target_compile_options(untitled-encore-native PRIVATE -Wall -Wextra -Wpedantic)
endif()
