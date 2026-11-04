# SPDX-License-Identifier: Apache-2.0
add_executable(telemd main.c sampler.c)
target_link_libraries(telemd PRIVATE core net rt m)
