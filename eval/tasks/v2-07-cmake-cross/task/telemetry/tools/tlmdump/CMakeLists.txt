# SPDX-License-Identifier: Apache-2.0
add_executable(tlmdump main.c)
target_link_libraries(tlmdump PRIVATE core net)
