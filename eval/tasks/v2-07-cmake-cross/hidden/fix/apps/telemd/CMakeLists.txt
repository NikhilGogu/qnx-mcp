# SPDX-License-Identifier: Apache-2.0
add_executable(telemd main.c sampler.c)
target_link_libraries(telemd PRIVATE core net m)
if(NOT CMAKE_SYSTEM_NAME STREQUAL "QNX")
  target_link_libraries(telemd PRIVATE rt)
endif()
