# SPDX-License-Identifier: Apache-2.0
cmake_minimum_required(VERSION 3.16)
project(telemetry C)

set(CMAKE_C_STANDARD 11)
set(CMAKE_C_EXTENSIONS ON)
add_compile_options(-Wall -Wextra)

add_subdirectory(libs/core)
add_subdirectory(libs/net)
add_subdirectory(apps/telemd)
add_subdirectory(tools/tlmdump)
